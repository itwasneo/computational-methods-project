// Both paths use instanced screen-space quads and a single O(n) synthetic polyline.
const glVertex = `#version 300 es
precision highp float;
layout(location=0) in vec2 point;
uniform vec4 transform;
uniform vec4 style; // clip-space radius x/y, selected ID, unused
out vec2 local;
flat out int selected;
const vec2 corners[6] = vec2[6](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(-1,1),vec2(1,-1),vec2(1,1));
void main() {
  local = corners[gl_VertexID];
  selected = int(float(gl_InstanceID) == style.z);
  gl_Position = vec4(point * transform.xy + transform.zw + local * style.xy, 0, 1);
}`;
const glFragment = `#version 300 es
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
  const shaders = [gl.VERTEX_SHADER, gl.FRAGMENT_SHADER].map((type, i) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, [vertex, fragment][i]); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  });
  const result = gl.createProgram();
  for (const shader of shaders) gl.attachShader(result, shader);
  gl.linkProgram(result);
  if (!gl.getProgramParameter(result, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(result));
  for (const shader of shaders) gl.deleteShader(shader);
  return result;
}

export function webgl(canvas, display, lost) {
  const gl = canvas.getContext('webgl2', {antialias: false, alpha: false});
  if (!gl) throw new Error('WebGL2 unavailable; no software/CPU fallback claimed');
  canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); lost('WebGL context lost; reload fixture to recover'); });
  const dots = program(gl, glVertex, glFragment), lines = program(gl, lineVertex, lineFragment);
  const buffer = gl.createBuffer(), vao = gl.createVertexArray();
  gl.bindVertexArray(vao); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, display, gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
  const count = display.length / 2;
  const locations = new Map([dots, lines].map(p => [p, gl.getUniformLocation(p, 'transform')]));
  const style = gl.getUniformLocation(dots, 'style');
  return {
    info: {api: 'WebGL2', renderer: gl.getParameter(gl.RENDERER), version: gl.getParameter(gl.VERSION),
      acceleration: 'Verify hardware acceleration in browser diagnostics; API availability alone is insufficient'},
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
    },
    destroy() { gl.deleteBuffer(buffer); gl.deleteVertexArray(vao); gl.deleteProgram(dots); gl.deleteProgram(lines); },
  };
}

const wgsl = `
struct Uniforms { transform: vec4f, style: vec4f };
@group(0) @binding(0) var<uniform> u: Uniforms;
struct Output { @builtin(position) position: vec4f, @location(0) local: vec2f, @location(1) @interpolate(flat) selected: u32 };
@vertex fn dots(@location(0) point: vec2f, @builtin(vertex_index) v: u32, @builtin(instance_index) i: u32) -> Output {
  let corners = array<vec2f,6>(vec2f(-1,-1),vec2f(1,-1),vec2f(-1,1),vec2f(-1,1),vec2f(1,-1),vec2f(1,1));
  var o: Output;
  o.local = corners[v]; o.selected = select(0u,1u,f32(i) == u.style.z);
  o.position = vec4f(point * u.transform.xy + u.transform.zw + o.local * u.style.xy,0,1);
  return o;
}
@fragment fn dotColor(o: Output) -> @location(0) vec4f {
  if (dot(o.local,o.local) > 1) { discard; }
  return select(vec4f(0.2,0.75,0.95,1),vec4f(1,0.6,0.1,1),o.selected == 1u);
}
@vertex fn line(@location(0) point: vec2f) -> @builtin(position) vec4f {
  return vec4f(point * u.transform.xy + u.transform.zw,0,1);
}
@fragment fn lineColor() -> @location(0) vec4f { return vec4f(0.2,0.35,0.45,1); }
`;
export async function webgpu(canvas, display, lost) {
  if (!navigator.gpu) throw new Error('WebGPU unavailable; explicitly choose WebGL2 to compare a fallback');
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) throw new Error('No WebGPU adapter');
  const device = await adapter.requestDevice();
  device.lost.then(info => lost(`WebGPU device lost: ${info.reason}; reload fixture to recover`));
  device.addEventListener('uncapturederror', event => lost(`WebGPU validation: ${event.error.message}`));
  const context = canvas.getContext('webgpu');
  if (!context) { device.destroy(); throw new Error('WebGPU canvas unavailable'); }
  const format = navigator.gpu.getPreferredCanvasFormat();
  context.configure({device, format, alphaMode: 'opaque'});
  const module = device.createShaderModule({code: wgsl});
  const diagnostics = await module.getCompilationInfo();
  const errors = diagnostics.messages.filter(message => message.type === 'error');
  if (errors.length) { device.destroy(); throw new Error(errors.map(e => e.message).join('\n')); }
  const count = display.length / 2;
  // Extra first point closes the strip. Its update is also only eight bytes.
  const buffer = device.createBuffer({size: display.byteLength + 8, usage: GPUBufferUsage.VERTEX | GPUBufferUsage.COPY_DST});
  device.queue.writeBuffer(buffer, 0, display);
  device.queue.writeBuffer(buffer, display.byteLength, display.subarray(0, 2));
  const uniform = device.createBuffer({size: 32, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST});
  const layout = device.createBindGroupLayout({entries: [{binding: 0, visibility: GPUShaderStage.VERTEX, buffer: {type: 'uniform'}}]});
  const pipelineLayout = device.createPipelineLayout({bindGroupLayouts: [layout]});
  const makePipeline = (entryPoint, fragment, topology, stepMode) => device.createRenderPipelineAsync({
    layout: pipelineLayout,
    vertex: {module, entryPoint, buffers: [{arrayStride: 8, stepMode, attributes: [{shaderLocation: 0, offset: 0, format: 'float32x2'}]}]},
    fragment: {module, entryPoint: fragment, targets: [{format}]}, primitive: {topology},
  });
  const dots = await makePipeline('dots', 'dotColor', 'triangle-list', 'instance');
  const lines = await makePipeline('line', 'lineColor', 'line-strip', 'vertex');
  const group = device.createBindGroup({layout, entries: [{binding: 0, resource: {buffer: uniform}}]});
  const values = new Float32Array(8);
  return {
    info: {api: 'WebGPU', adapter: adapter.info ? {vendor: adapter.info.vendor, architecture: adapter.info.architecture,
      device: adapter.info.device, description: adapter.info.description} : null,
      acceleration: 'Verify hardware adapter in browser diagnostics'},
    update(id) {
      device.queue.writeBuffer(buffer, id * 8, display.subarray(id * 2, id * 2 + 2));
      if (id === 0) device.queue.writeBuffer(buffer, display.byteLength, display.subarray(0, 2));
    },
    draw(transform, selected, tour, dpr) {
      values.set(transform); values.set([7 * dpr / canvas.width, 7 * dpr / canvas.height, selected, 0], 4);
      device.queue.writeBuffer(uniform, 0, values);
      const encoder = device.createCommandEncoder();
      const pass = encoder.beginRenderPass({colorAttachments: [{view: context.getCurrentTexture().createView(),
        clearValue: {r: 0.035, g: 0.06, b: 0.09, a: 1}, loadOp: 'clear', storeOp: 'store'}]});
      pass.setBindGroup(0, group); pass.setVertexBuffer(0, buffer);
      if (tour) { pass.setPipeline(lines); pass.draw(count + 1); }
      pass.setPipeline(dots); pass.draw(6, count); pass.end(); device.queue.submit([encoder.finish()]);
    },
    destroy() { context.unconfigure(); buffer.destroy(); uniform.destroy(); device.destroy(); },
  };
}
