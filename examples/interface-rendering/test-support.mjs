// Fault-injection doubles only. They do not compile shaders or certify GPU behavior.
export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
export function globals(t, values) {
  for (const [key, value] of Object.entries(values)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, {value, configurable: true, writable: true});
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
}
export function gpuEnvironment(t, devices) {
  globals(t, {
    GPUBufferUsage: {VERTEX: 1, COPY_DST: 2, UNIFORM: 4}, GPUShaderStage: {VERTEX: 1},
    navigator: {gpu: {
      async requestAdapter() { const device = devices.shift(); return {async requestDevice() { return device; }}; },
      getPreferredCanvasFormat() { return 'bgra8unorm'; },
    }},
  });
}
export function gpuDevice({compilation = Promise.resolve({messages: []}), failPipeline = 0} = {}) {
  const loss = deferred(), listeners = new Map();
  let pipelines = 0;
  const device = {
    buffers: [], draws: [], destroyed: 0, lost: loss.promise,
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name) { listeners.delete(name); },
    error(message) { listeners.get('uncapturederror')?.({error: {message}}); },
    destroy() { this.destroyed++; loss.resolve({reason: 'destroyed'}); },
    createShaderModule() { return {getCompilationInfo() { return compilation; }}; },
    createBuffer() { const buffer = {destroyed: 0, destroy() { this.destroyed++; }}; this.buffers.push(buffer); return buffer; },
    createBindGroupLayout() { return {}; }, createPipelineLayout() { return {}; }, createBindGroup() { return {}; },
    async createRenderPipelineAsync(descriptor) {
      if (++pipelines === failPipeline) throw new Error('pipeline failure');
      return descriptor.vertex.entryPoint;
    },
    queue: {writeBuffer() {}, submit() {}},
    createCommandEncoder() {
      let pipeline;
      return {beginRenderPass: () => ({
        setBindGroup() {}, setVertexBuffer() {}, setPipeline(value) { pipeline = value; },
        draw: (...args) => device.draws.push({pipeline, args}), end() {},
      }), finish() { return {}; }};
    },
  };
  return device;
}
export function gpuCanvas() {
  const context = {unconfigured: 0, configure() {}, unconfigure() { this.unconfigured++; },
    getCurrentTexture() { return {createView() { return {}; }}; }};
  return {width: 1000, height: 600, context, getContext() { return context; }};
}
export function glCanvas({failShader = 0} = {}) {
  const resources = [], draws = [], listeners = new Map();
  let shaders = 0, offset = 0, style;
  const resource = () => { const item = {deleted: 0}; resources.push(item); return item; };
  const remove = item => { item.deleted++; };
  const gl = {
    ...Object.fromEntries(['VERTEX_SHADER', 'FRAGMENT_SHADER', 'COMPILE_STATUS', 'LINK_STATUS', 'ARRAY_BUFFER',
      'DYNAMIC_DRAW', 'FLOAT', 'RENDERER', 'VERSION', 'COLOR_BUFFER_BIT', 'LINE_LOOP', 'TRIANGLES'].map(key => [key, key])),
    createShader() { shaders++; return resource(); }, createProgram: resource, createBuffer: resource, createVertexArray: resource,
    deleteShader: remove, deleteProgram: remove, deleteBuffer: remove, deleteVertexArray: remove,
    shaderSource() {}, compileShader() {}, attachShader() {}, linkProgram() {},
    getShaderParameter() { return shaders !== failShader; }, getShaderInfoLog() { return 'shader failure'; },
    getProgramParameter() { return true; }, getUniformLocation() { return {}; }, getParameter() { return 'test'; },
    bindVertexArray() {}, bindBuffer() {}, bufferData() {}, bufferSubData() {}, enableVertexAttribArray() {},
    vertexAttribPointer(...args) { offset = args[5]; }, vertexAttribDivisor() {},
    viewport() {}, clearColor() {}, clear() {}, useProgram() {}, uniform4fv() {}, uniform4f(...args) { style = args.slice(1); },
    drawArrays(...args) { draws.push({args, offset}); }, drawArraysInstanced(...args) { draws.push({args, offset, style}); },
  };
  return {width: 1000, height: 600, gl, resources, draws, listeners, getContext() { return gl; },
    addEventListener(name, callback) { listeners.set(name, callback); }, removeEventListener(name) { listeners.delete(name); }};
}
