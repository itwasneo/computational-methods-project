"""Versioned content fingerprints and schedule-independent RNG ownership."""

from dataclasses import FrozenInstanceError
import random

from numba import njit
import numpy as np
import pytest

from tsp_hpc.data import TSPInstance
from tsp_hpc.identity import coordinate_checksum, describe_instance, make_instance_id
from tsp_hpc.random_streams import RandomStream, derive_stream, validate_seed


@pytest.fixture
def square():
    return np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]])


def test_coordinate_and_instance_identity_golden_vectors(square):
    assert coordinate_checksum(square) == "5bd594629b558cec3174cb988c57d2c4dc5e8ef38a0d352d32ee9e535ddc5275"
    assert make_instance_id(square) == "instance-v1-434c24120a05da693ed0f22582acc664ddef0f054a900905b4cf275ba68f5699"
    assert coordinate_checksum(square.copy()) == coordinate_checksum(square)
    assert make_instance_id(square, "tsplib_euc_2d") != make_instance_id(square)
    assert coordinate_checksum(square[::-1].copy()) != coordinate_checksum(square)
    changed = square.copy()
    changed[0, 0] = -0.0
    assert coordinate_checksum(changed) != coordinate_checksum(square)
    changed[0, 0] = 0.1
    assert make_instance_id(changed) != make_instance_id(square)
    assert coordinate_checksum(square[:2].copy()) != coordinate_checksum(square)


def test_describe_instance_preserves_input_and_separates_seed_metadata(square):
    before = square.copy()
    problem = TSPInstance("square", square)
    descriptor = describe_instance(problem, instance_seed=2**64 - 1)
    assert descriptor == {
        "instance_id": "square", "n_cities": 4, "distance_kind": "euclidean_2d",
        "coordinate_checksum": coordinate_checksum(square), "instance_seed": 2**64 - 1,
    }
    assert describe_instance(problem)["instance_seed"] is None
    assert square.flags.writeable
    np.testing.assert_array_equal(square, before)
    descriptor["instance_id"] = "changed"
    assert problem.instance_id == "square"


def test_instance_descriptor_normalizes_supported_string_subclasses(square):
    problem = TSPInstance(np.str_("square"), square, np.str_("euclidean_2d"))
    descriptor = describe_instance(problem)
    assert type(descriptor["instance_id"]) is str
    assert type(descriptor["distance_kind"]) is str
    assert descriptor == describe_instance(TSPInstance("square", square))


@pytest.mark.parametrize("seed,error", [
    (True, TypeError), (np.int64(1), TypeError), (1.0, TypeError),
    (None, TypeError), (-1, ValueError), (2**64, ValueError),
])
def test_invalid_seed_values(seed, error, square):
    with pytest.raises(error):
        validate_seed(seed)
    with pytest.raises(error):
        derive_stream(seed, "chain", "a")
    if seed is not None:
        with pytest.raises(error):
            describe_instance(TSPInstance("square", square), instance_seed=seed)


@pytest.mark.parametrize("seed", [0, 1, 2**32 + 1, 2**64 - 1])
def test_full_uint64_seed_range(seed):
    validate_seed(seed)
    stream = derive_stream(seed, "solver", "a")
    assert type(stream.seed) is int and 0 <= stream.seed < 2**64


def test_high_seed_bits_are_not_discarded():
    assert derive_stream(1, "chain", "a") != derive_stream(2**32 + 1, "chain", "a")


def test_stream_and_pcg64_golden_vectors():
    stream = derive_stream(123, "chain", "example-instance", 2, "chain-a")
    assert stream.stream_id == "stream-v1-fb674f9a24ebda82a43a5ae0d01f91eba6a4134679edf3bc6c0759d489c55eb9"
    assert stream.seed == 18115535549584104066
    assert stream.generator().bit_generator.random_raw(4).tolist() == [
        3687149251324477503, 12030575299057190752, 17778425819332579770, 8582470226935123620,
    ]
    with pytest.raises(FrozenInstanceError):
        stream.seed = 0


@pytest.mark.parametrize("identifier,seed,error", [
    ("stream-v1-" + "0" * 64, None, TypeError),
    (None, 0, TypeError), ("invalid", 0, ValueError),
    ("stream-v1-" + "0" * 64, 1, ValueError),
])
def test_invalid_stream_records_cannot_create_unidentified_rng_state(identifier, seed, error):
    with pytest.raises(error):
        RandomStream(identifier, seed)


def test_roles_and_framed_ids_separate_streams():
    streams = [derive_stream(7, role, index) for role in ("instance", "solver", "start", "chain", "island")
               for index in range(200)]
    assert len({stream.stream_id for stream in streams}) == 1000
    assert len({stream.seed for stream in streams}) == 1000
    assert derive_stream(7, "chain", "ab", "c") != derive_stream(7, "chain", "a", "bc")
    assert derive_stream(7, "chain", 1) != derive_stream(7, "chain", "1")
    assert derive_stream(7, "chain", 1, 2) != derive_stream(7, "chain", 2, 1)


@pytest.mark.parametrize("role,ids,error", [
    (None, (), TypeError), (" ", (), ValueError),
    ("chain", (True,), TypeError), ("chain", (1.0,), TypeError),
    ("chain", (np.int64(1),), TypeError), ("chain", ([],), TypeError),
    ("chain", (-1,), ValueError), ("chain", ("",), ValueError),
])
def test_invalid_stream_paths(role, ids, error):
    with pytest.raises(error):
        derive_stream(0, role, *ids)


def test_generators_are_fresh_owned_state_without_global_rng_changes():
    numpy_before = np.random.get_state()
    python_before = random.getstate()
    stream = derive_stream(42, "chain", "a")
    first, second = stream.generator(), stream.generator()
    assert first is not second
    expected = second.random(10)
    np.testing.assert_array_equal(first.random(5), expected[:5])
    np.testing.assert_array_equal(first.random(5), expected[5:])
    # A new generator restarts the stream, rather than continuing either object.
    np.testing.assert_array_equal(stream.generator().random(10), expected)
    numpy_after = np.random.get_state()
    assert numpy_before[0] == numpy_after[0]
    np.testing.assert_array_equal(numpy_before[1], numpy_after[1])
    assert numpy_before[2:] == numpy_after[2:]
    assert python_before == random.getstate()


@njit
def _compiled_draw(generator):
    return generator.random()


def test_owned_generator_can_be_passed_to_a_serial_numba_kernel():
    stream = derive_stream(5, "chain", "numba-check")
    compiled, reference = stream.generator(), stream.generator()
    for _ in range(3):
        assert _compiled_draw(compiled) == reference.random()
    assert _compiled_draw.nopython_signatures


def test_content_helpers_reject_invalid_inputs(square):
    with pytest.raises(TypeError):
        coordinate_checksum(square.tolist())
    with pytest.raises(ValueError):
        coordinate_checksum(np.array([[0., 0.], [np.nan, 0.]]))
    with pytest.raises(ValueError):
        make_instance_id(square, "unknown")
    with pytest.raises(TypeError):
        make_instance_id(square, None)
    with pytest.raises(TypeError):
        describe_instance(None)
