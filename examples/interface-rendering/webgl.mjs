// Small WebGL2 reference: instanced cities, one closed polyline, selection last.
const vertex = `#version 300 es
precision highp float;
layout(location=0) in vec2 point;
uniform vec4 transform;
uniform vec4 style; // clip-space radius x/y, selected ID, first instance
out vec2 local;
flat out int selected;
const vec2 corners[6] = vec2[6](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(-1,1),vec2(1,-1),vec2(1,1));
void main() {
  local = corners[gl_VertexID];
  selected = int(float(gl_InstanceID) + style.w == style.z);
  gl_Position = vec4(point * transform.xy + transform.zw + local * style.xy, 0, 1);
}`;
const fragment = `#version 300 es
precision highp float;
in vec2 local;
flat in int selected;
out vec4 color;
void main() {
  if (dot(local,local) > 1.0) discard;
  color = selected == 1 ? vec4(1,0.6,0.1,1) : vec4(0.2,0.75,0.95,1);
}`;
const lineVertex = `#version 300 es
layout(location=0) in vec2 point;
uniform vec4 transform;
void main() { gl_Position = vec4(point * transform.xy + transform.zw, 0, 1); }`;
const lineFragment = `#version 300 es
precision highp float;
out vec4 color;
void main() { color = vec4(0.2,0.35,0.45,1); }`;
function program(gl, vertex, fragment) {
  const shaders = [], result = gl.createProgram();
  try {
    for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
      const shader = gl.createShader(type); shaders.push(shader);
      gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      gl.attachShader(result, shader);
    }
    gl.linkProgram(result);
    if (!gl.getProgramParameter(result, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(result));
    return result;
  } catch (error) { gl.deleteProgram(result); throw error; }
  finally { for (const shader of shaders) gl.deleteShader(shader); }
}

export function webgl(canvas, display, lost) {
  const gl = canvas.getContext('webgl2', {antialias: false, alpha: false});
  if (!gl) throw new Error('WebGL2 unavailable; no software/CPU fallback claimed');
  let dots, lines, buffer, vao, disposed = false;
  const onLoss = event => { event.preventDefault(); if (!disposed) lost('WebGL context lost; reload fixture to recover'); };
  const destroy = () => {
    if (disposed) return;
    disposed = true; canvas.removeEventListener('webglcontextlost', onLoss);
    if (buffer) gl.deleteBuffer(buffer);
    if (vao) gl.deleteVertexArray(vao);
    if (dots) gl.deleteProgram(dots);
    if (lines) gl.deleteProgram(lines);
  };
  canvas.addEventListener('webglcontextlost', onLoss);
  try {
    dots = program(gl, vertex, fragment); lines = program(gl, lineVertex, lineFragment);
    buffer = gl.createBuffer(); vao = gl.createVertexArray();
    gl.bindVertexArray(vao); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, display, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    const count = display.length / 2;
    const locations = new Map([dots, lines].map(p => [p, gl.getUniformLocation(p, 'transform')]));
    const style = gl.getUniformLocation(dots, 'style');
    return {
      update(id) {
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferSubData(gl.ARRAY_BUFFER, id * 8, display.subarray(id * 2, id * 2 + 2));
      },
      draw(transform, selected, tour, dpr) {
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.clearColor(0.035, 0.06, 0.09, 1); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.bindVertexArray(vao);
        if (tour) {
          gl.useProgram(lines); gl.uniform4fv(locations.get(lines), transform);
          gl.vertexAttribDivisor(0, 0); gl.drawArrays(gl.LINE_LOOP, 0, count);
        }
        gl.useProgram(dots); gl.uniform4fv(locations.get(dots), transform);
        gl.uniform4f(style, 7 * dpr / canvas.width, 7 * dpr / canvas.height, selected, 0);
        gl.vertexAttribDivisor(0, 1); gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, count);
        if (Number.isInteger(selected) && selected >= 0 && selected < count) {
          gl.uniform4f(style, 7 * dpr / canvas.width, 7 * dpr / canvas.height, selected, selected);
          gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, selected * 8);
          gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, 1);
          gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
        }
      },
      destroy,
    };
  } catch (error) { destroy(); throw error; }
}
