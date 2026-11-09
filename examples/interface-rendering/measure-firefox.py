"""One-off Firefox decision diagnostics, not UI acceptance or solver benchmarks.

Requires Node 24 (WebSocket), local geckodriver/Firefox and the project's uv env.
Keep results outside Git. No acceleration/privacy flags, browser downloads or
forced GC. WebDriver BiDi emulates viewport/DPR; RAF is not presentation time.
"""
import argparse
from datetime import datetime, timezone
import functools
import hashlib
import http.server
import json
import math
import os
from pathlib import Path
import signal
import select
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def stats(values):
    values = sorted(values)
    return {"samples": len(values), **{name: values[min(len(values)-1, math.ceil(len(values)*q)-1)] if values else None
            for name, q in [("p50", .5), ("p95", .95), ("p99", .99), ("max", 1)]}}

def tree(root):
    rows = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            rows[int(path.parent.name)] = (int(fields[1]), int(fields[11])+int(fields[12]), int(fields[21])*os.sysconf('SC_PAGE_SIZE'))
        except (OSError, ValueError):
            pass
    ids = {root}
    while True:
        expanded = ids | {pid for pid, (parent, _, _) in rows.items() if parent in ids}
        if expanded == ids:
            break
        ids = expanded
    return {"rss_bytes": sum(rows[pid][2] for pid in ids if pid in rows),
            "cpu_s": sum(rows[pid][1] for pid in ids if pid in rows)/os.sysconf('SC_CLK_TCK'),
            "processes": len(ids)}

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

class Browser:
    def __init__(self, dpr, driver, address):
        self.session = None
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        self.endpoint = f'http://127.0.0.1:{port}'
        self.driver = subprocess.Popen([driver, '--host', '127.0.0.1', '--port', str(port), '--log', 'error'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    self.call('GET', '/status'); break
                except OSError:
                    time.sleep(.2)
            opened = self.call('POST', '/session', {'capabilities': {'alwaysMatch': {
                'browserName': 'firefox', 'webSocketUrl': True, 'moz:firefoxOptions': {'args': []}}}})
            self.session = opened['sessionId']; self.root = '/session/'+self.session
            self.pid = opened['capabilities']['moz:processID']
            self.version = opened['capabilities']['browserVersion']
            self.call('POST', self.root+'/window/fullscreen', {})
            context = self.call('GET', self.root+'/window')
            command = """const w=new WebSocket(process.argv[1]);
              w.onopen=()=>w.send(JSON.stringify({id:1,method:'browsingContext.setViewport',params:{
                context:process.argv[2],viewport:{width:1440,height:900},devicePixelRatio:Number(process.argv[3])}}));
              w.onmessage=e=>{console.log(e.data);w.close()};"""
            result = subprocess.run(['node', '--input-type=module', '-e', command,
                opened['capabilities']['webSocketUrl'], context, str(dpr)], check=True, capture_output=True, text=True, timeout=10)
            assert json.loads(result.stdout)['type'] == 'success', result.stdout
            self.blank = tree(self.pid)
            self.call('POST', self.root+'/url', {'url': address})
            self.wait_ready()
            self.environment = self.js("return {viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,hidden:document.hidden,focused:document.hasFocus(),webgpu:!!navigator.gpu}")
            assert self.environment['viewport'] == [1440,900] and self.environment['dpr'] == dpr
            assert not self.environment['hidden'], self.environment
            self.environment['timerQuantumMs'] = self.js("const t=performance.now(),ds=[];let old=t;while(performance.now()-t<20){let now=performance.now();if(now>old)ds.push(now-old);old=now}return Math.min(...ds)")
        except BaseException:
            self.close(); raise

    def call(self, method, path, body=None):
        request = urllib.request.Request(self.endpoint+path, method=method,
            data=None if body is None else json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.load(response)['value']

    def js(self, script, args=None, asynchronous=False):
        return self.call('POST', self.root+'/execute/'+('async' if asynchronous else 'sync'), {'script':script,'args':args or []})

    def wait_ready(self):
        for _ in range(100):
            state = self.js("return {busy:document.getElementById('load').disabled,status:document.getElementById('status').textContent}")
            if not state['busy']:
                assert state['status'].startswith('Ready:'), state
                return
            time.sleep(.1)
        raise TimeoutError('fixture load')

    def load(self, count, kind, tour):
        result = self.js("""const [n,kind,tour,done]=arguments; (async()=>{
          const $=id=>document.getElementById(id),start=performance.now();
          $('count').value=n;$('kind').value=kind;$('tour').checked=tour;
          await $('load').onclick();requestAnimationFrame(()=>done({requestToRafMs:performance.now()-start,
            status:$('status').textContent,viewport:[innerWidth,innerHeight],dpr:devicePixelRatio,hidden:document.hidden}));
        })().catch(e=>done({error:e.message}));""", [count,kind,tour], True)
        assert result['status'].startswith('Ready:') and not result['hidden'], result
        assert result['viewport'] == [1440,900]
        return result

    def diagnostic(self, count, kind, tour, label):
        opened = self.load(count, kind, tour)
        start = tree(self.pid); peak = start['rss_bytes']
        self.js("document.getElementById('measure').click()")
        end_at = time.monotonic()+10.2
        while time.monotonic() < end_at:
            peak = max(peak, tree(self.pid)['rss_bytes']); time.sleep(.25)
        result = self.js("return {report:document.getElementById('report').textContent,status:document.getElementById('status').textContent,hidden:document.hidden}")
        assert result['report'] and not result['hidden'], result
        report = json.loads(result['report'])
        assert report['viewport'] == {'width':1440,'height':900,'dpr':opened['dpr']}
        return {"label":label, "open":opened, "diagnostic":report,
                "rss_start_bytes":start['rss_bytes'],"rss_peak_bytes":peak,"rss_end_bytes":tree(self.pid)['rss_bytes']}

    def inputs(self, kind, move=False):
        self.load(50000, kind, True)
        positions = self.js("""const done=arguments[0]; (async()=>{
          const {fixture,bounds}=await import('./model.mjs'),$=id=>document.getElementById(id);
          const p=fixture(50000,$('kind').value),b=bounds(p),c=document.querySelector('canvas'),r=c.getBoundingClientRect();
          const side=Math.min(c.clientWidth,c.clientHeight)*.85;
          done(Array.from({length:100},(_,i)=>({x:Math.floor(r.left+r.width/2+(p[2*i]-b.x)/b.span*side),
            y:Math.floor(r.top+r.height/2-(p[2*i+1]-b.y)/b.span*side)})));
        })().catch(e=>done({error:e.message}));""", asynchronous=True)
        self.js("""window.feedback=[];window.capture=e=>{
          if(!e.isTrusted || (e.type==='pointermove'&&!e.buttons))return;
          const start=performance.now();queueMicrotask(()=>requestAnimationFrame(()=>{
            const first=performance.now()-start;requestAnimationFrame(()=>feedback.push({first,second:performance.now()-start}));
          }));
        };document.querySelector('canvas').addEventListener(arguments[0],capture,true);""", ['pointermove' if move else 'pointerdown'])
        if move:
            x,y=positions[0]['x'],positions[0]['y']
            actions=[{'type':'pointerMove','duration':0,'x':x,'y':y},{'type':'pointerDown','button':0}]
            actions += [{'type':'pointerMove','duration':20,'x':x+(i%10),'y':y+(i%7)} for i in range(100)]
            actions += [{'type':'pointerUp','button':0},{'type':'pause','duration':100}]
            keys=[{'type':'keyDown','value':'\ue008'}]+[{'type':'pause','duration':0}]*(len(actions)-2)+[{'type':'keyUp','value':'\ue008'}]
            sources=[{'type':'key','id':'keys','actions':keys}]
        else:
            actions=[]
            for position in positions:
                actions += [{'type':'pointerMove','duration':0,**position},{'type':'pointerDown','button':0},
                            {'type':'pointerUp','button':0},{'type':'pause','duration':50}]
            sources=[]
        sources.append({'type':'pointer','id':'mouse','parameters':{'pointerType':'mouse'},'actions':actions})
        self.call('POST',self.root+'/actions',{'actions':sources});time.sleep(.15)
        samples=self.js("document.querySelector('canvas').removeEventListener(arguments[0],capture,true);return feedback",['pointermove' if move else 'pointerdown'])
        self.call('DELETE',self.root+'/actions')
        assert len(samples)>=90, len(samples)
        return {'kind':kind,'action':'shift-drag' if move else 'pick', 'first_raf_ms':stats([s['first'] for s in samples]),
                'second_raf_ms':stats([s['second'] for s in samples]),'note':'Trusted WebDriver input to RAF opportunities, NOT physical presentation latency'}

    def concurrent_nn(self):
        code = '''
import os,time
os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})
from tsp_hpc.problem.instances import uniform_instance
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.solvers.nn.solver import NearestNeighbourSolver
instance=uniform_instance(2000,seed=12345).instance
solver=NearestNeighbourSolver(prepare_distances(instance,mode="on_demand"))
print("ready",flush=True)
end=time.monotonic()+30
while time.monotonic()<end:
    result=solver.solve(instance,seed=42,budget={"start_cities":list(range(64))},parameters={})
    assert result.status=="ok"
'''
        environment = {**os.environ, **{key:'1' for key in ['NUMBA_NUM_THREADS','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']}}
        with subprocess.Popen([str(ROOT/'.venv/bin/python'),'-c',code],cwd=ROOT,env=environment,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as worker:
            try:
                assert select.select([worker.stdout],[],[],30)[0], 'NN warmup timeout'
                assert worker.stdout.readline().strip()=='ready', 'NN worker failed to initialize'
                record=self.diagnostic(10000,'uniform',True,'one-NN-worker')
                assert worker.poll() is None, 'NN worker did not cover entire interaction window'
                record['worker']={'algorithm':'NN visited-scan-v1','cities':2000,'starts_per_solve':64,
                    'distance_mode':'on_demand','thread_limits':1,'affinity':[max(os.sched_getaffinity(0))],
                    'bounded_seconds':30,'note':'Warmup precedes interaction; disposable compute load, no benchmark records; terminated afterward'}
                return record
            finally:
                worker.terminate()
                try:worker.wait(timeout=5)
                except subprocess.TimeoutExpired:worker.kill();worker.wait()

    def close(self):
        try:
            if self.session:
                self.call('DELETE','/session/'+self.session)
        finally:
            self.driver.terminate()
            try:self.driver.wait(timeout=5)
            except subprocess.TimeoutExpired:self.driver.kill();self.driver.wait()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--driver',default='geckodriver')
    parser.add_argument('--quick',action='store_true')
    args=parser.parse_args()
    state={'schema':'firefox-rendering-decision-v1','complete':False,'started_at':datetime.now(timezone.utc).isoformat(),
           'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
           'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
           'sources':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ['app.mjs','model.mjs','renderers.mjs','index.html','measure-firefox.py']},
           'method':'visible Firefox; BiDi viewport/DPR emulation; normal acceleration/privacy; fresh profile per DPR; warm sequential fixture resets; no filesystem-cache flush',
           'limitations':['RAF/input proxies are not GPU/display presentation; default timer precision retained',
             'RSS is a sum over unique browser-tree PIDs and may double-count shared mappings; no GPU allocation attribution',
             'One sweep plus focused repeats, not five cold/five warm acceptance trials; no background-worker result unless explicitly recorded'],
           'profiles':[]}
    with args.output.open('x') as output:
        def save():
            output.seek(0);json.dump(state,output,indent=2);output.truncate();output.flush()
        save()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(HERE)))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            for dpr in ([1] if args.quick else [1,2]):
                browser=Browser(dpr,args.driver,f'http://127.0.0.1:{server.server_port}')
                profile={'dpr':dpr,'browser':browser.version,'environment':browser.environment,'blank_browser':browser.blank,'trials':[],'inputs':[]}
                state['profiles'].append(profile);save()
                try:
                    matrix=[(1000,'uniform',False)] if args.quick else [(n,k,t) for n in [1000,10000,50000]
                      for k in ['uniform','clustered','duplicates','collinear','coincident'] for t in ([False,True] if k in ['uniform','coincident'] else [True])]
                    for n,kind,tour in matrix:
                        profile['trials'].append(browser.diagnostic(n,kind,tour,'sweep'));save()
                        print(dpr,n,kind,tour,profile['trials'][-1]['diagnostic']['rafIntervalMs'],flush=True)
                    if not args.quick:
                        for _ in range(4):
                            profile['trials'].append(browser.diagnostic(50000,'coincident',True,'repeat'));save()
                        for kind in ['uniform','coincident']:
                            for move in [False,True]:
                                profile['inputs'].append(browser.inputs(kind,move));save()
                        profile['trials'].append(browser.concurrent_nn());save()
                        browser.load(50000,'uniform',True);time.sleep(30)
                        first=tree(browser.pid)
                        for _ in range(20):browser.load(50000,'uniform',True)
                        time.sleep(30);last=tree(browser.pid)
                        profile['retention']={'first':first,'after_20_resets':last,'growth_bytes':last['rss_bytes']-first['rss_bytes']};save()
                        start=tree(browser.pid);clock=time.monotonic();time.sleep(60);end=tree(browser.pid)
                        profile['idle']={'seconds':time.monotonic()-clock,'one_core_cpu_percent':100*(end['cpu_s']-start['cpu_s'])/(time.monotonic()-clock)};save()
                finally:browser.close()
            state['complete']=True;save()
        finally:server.shutdown();server.server_close();save()

if __name__=='__main__':
    def interrupt(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    main()
