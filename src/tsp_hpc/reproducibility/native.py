"""Observed compiler configuration and explicit native-dispatch verification.

Validators import no compiler/solver implementation. Observations are not cache,
hardware or source attestation; a trusted fixed process/cache remains required.
"""


class NativeExecutionError(RuntimeError):
    """A declared native implementation cannot establish its execution mode."""


_CONFIG_BOOLEANS = {"disable_jit", "loop_vectorize", "slp_vectorize", "enable_avx", "debug_info"}
_CONFIG_TEXT = {"cpu_name", "cpu_features"}
_CONFIG_FIELDS = _CONFIG_BOOLEANS | _CONFIG_TEXT | {"optimization", "boundscheck"}
_OPTIONS = {"fastmath", "parallel", "boundscheck", "nogil", "error_model"}


def compiler_observation():
    import numba
    import llvmlite.binding as llvm
    config = numba.config
    try:
        host_name, host_features = llvm.get_host_cpu_name(), llvm.get_host_cpu_features().flatten()
    except (OSError, RuntimeError):
        host_name = host_features = None
    return {"schema_version": 1, "provider": "numba", "host_cpu_name": host_name,
            "host_cpu_features": host_features, "config": {
                "disable_jit": bool(config.DISABLE_JIT), "boundscheck": None if config.BOUNDSCHECK is None else bool(config.BOUNDSCHECK),
                "optimization": "max" if getattr(config.OPT, "is_opt_max", False) else str(int(config.OPT)),
                "cpu_name": config.CPU_NAME, "cpu_features": config.CPU_FEATURES,
                "loop_vectorize": bool(config.LOOP_VECTORIZE), "slp_vectorize": bool(config.SLP_VECTORIZE),
                "enable_avx": bool(config.ENABLE_AVX), "debug_info": bool(config.DEBUGINFO_DEFAULT)}}


def require_native_runtime():
    import numba
    if numba.config.DISABLE_JIT:
        raise NativeExecutionError("NUMBA_DISABLE_JIT is enabled; native baselines cannot use interpreted execution")


def verify_dispatchers(dispatchers):
    """Inspect the actually warmed selected CPU dispatchers, not module aliases."""
    from numba.core.registry import CPUDispatcher
    require_native_runtime()
    if type(dispatchers) is not tuple or not dispatchers:
        raise NativeExecutionError("native adapters must expose a nonempty tuple of selected native_dispatchers")
    observations = []
    for dispatcher in dispatchers:
        if not isinstance(dispatcher, CPUDispatcher) or not dispatcher.nopython_signatures or (
            len(dispatcher.nopython_signatures) != len(dispatcher.signatures)
        ):
            raise NativeExecutionError("selected kernel is not a warmed nopython CPU dispatcher")
        options = dispatcher.targetoptions
        if options.get("fastmath", False) is not False or options.get("parallel", False) is not False:
            raise NativeExecutionError("serial correctness baselines require fastmath=False and parallel=False")
        observations.append({"name": f"{dispatcher.py_func.__module__}.{dispatcher.py_func.__qualname__}",
                             "nopython_signatures": [str(signature) for signature in dispatcher.nopython_signatures],
                             "options": {"fastmath": False, "parallel": False,
                                         "boundscheck": options.get("boundscheck"), "nogil": options.get("nogil", False),
                                         "error_model": options.get("error_model", "python")}})
    return observations


def adapter_native_observation(adapter, spec):
    if not spec.native:
        return {"required": False, "verified": False, "dispatchers": []}
    try:
        dispatchers = adapter.native_dispatchers
    except AttributeError as error:
        raise NativeExecutionError("native adapter must expose its selected native_dispatchers") from error
    return {"required": True, "verified": True, "dispatchers": verify_dispatchers(dispatchers)}


def _fields(value, names):
    if type(value) is not dict or value.keys() != set(names):
        raise ValueError("invalid compiler/native observation fields")


def _text(value):
    if type(value) is not str or not value.strip():
        raise ValueError("expected nonempty native observation text")


def validate_compiler_observation(value):
    _fields(value, {"schema_version", "provider", "host_cpu_name", "host_cpu_features", "config"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["provider"] != "numba":
        raise ValueError("unsupported compiler observation schema/provider")
    for key in ("host_cpu_name", "host_cpu_features"):
        if value[key] is not None and type(value[key]) is not str:
            raise ValueError("invalid host target observation")
    config = value["config"]
    _fields(config, _CONFIG_FIELDS)
    if any(type(config[key]) is not bool for key in _CONFIG_BOOLEANS):
        raise ValueError("compiler flags must be boolean")
    if config["boundscheck"] is not None and type(config["boundscheck"]) is not bool:
        raise ValueError("invalid effective bounds-check policy")
    if any(config[key] is not None and type(config[key]) is not str for key in _CONFIG_TEXT):
        raise ValueError("invalid CPU targeting configuration")
    if type(config["optimization"]) is not str or config["optimization"] not in ("0", "1", "2", "3", "max"):
        raise ValueError("invalid effective optimization level")


def validate_native_observation(value):
    _fields(value, {"required", "verified", "dispatchers"})
    if any(type(value[key]) is not bool for key in ("required", "verified")) or (value["verified"] and not value["required"]):
        raise ValueError("inconsistent native verification claim")
    dispatchers = value["dispatchers"]
    if type(dispatchers) is not list or bool(dispatchers) != value["verified"]:
        raise ValueError("missing/unexpected native dispatcher observations")
    for dispatcher in dispatchers:
        _fields(dispatcher, {"name", "nopython_signatures", "options"})
        _text(dispatcher["name"])
        signatures = dispatcher["nopython_signatures"]
        if type(signatures) is not list or not signatures:
            raise ValueError("missing nopython signatures")
        for signature in signatures:
            _text(signature)
        options = dispatcher["options"]
        _fields(options, _OPTIONS)
        if options["fastmath"] is not False or options["parallel"] is not False or type(options["nogil"]) is not bool:
            raise ValueError("invalid serial native flags")
        if options["boundscheck"] is not None and type(options["boundscheck"]) is not bool:
            raise ValueError("invalid native bounds-check option")
        if options["error_model"] not in ("python", "numpy"):
            raise ValueError("unsupported native error model")
