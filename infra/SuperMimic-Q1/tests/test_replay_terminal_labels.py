"""CPU regressions for ReplayRecorder's source-row done_next contract.

Use real buffer writes, episode counters, masks and sampling (no mocked sampler).
Terminal sentinel rows carry no outgoing action, including on timeouts.
"""
import pytest
import torch

from supermimic.data.replay_buffer import ReplayBuffer


def add_row(buffer, state, done=0, terminal=False, start=False):
    ids = torch.tensor([0])
    scalar = torch.tensor([float(state)])
    buffer.add(ids, scalar[:, None], (scalar + 10)[:, None],
               (scalar + 20)[:, None], torch.zeros(1, 1), scalar + 30,
               torch.tensor([float(done)]), torch.tensor([state]), ids,
               torch.tensor([terminal]), torch.tensor([start]))


def assert_window(buffer, states, done):
    # Exactly one valid window, so no dependence on RNG or sampling luck.
    k = len(done)
    assert buffer.window_mask(k).sum().item() == 1
    w = buffer.sample_windows(1, k)
    assert w.states[:, 0, 0].tolist() == states
    assert w.a_exec[:, 0, 0].tolist() == [s + 20 for s in states[:-1]]
    assert w.reward[:, 0].tolist() == [s + 30 for s in states[:-1]]
    assert w.done[:, 0].tolist() == done


def test_normal_then_death_not_early_or_duplicated():
    b = ReplayBuffer(1, 16, 1, 1, 1, 'cpu')
    add_row(b, 0, start=True)
    add_row(b, 1, done=1)
    add_row(b, 2, done=1, terminal=True)
    b.end_episode(torch.tensor([0]))
    assert_window(b, [0, 1, 2], [0, 1])


def test_ordinary_nonterminal_transitions():
    b = ReplayBuffer(1, 16, 1, 1, 1, 'cpu')
    for s in range(3):
        add_row(b, s, start=s == 0)
    assert_window(b, [0, 1, 2], [0, 0])


def test_timeout_is_episode_boundary_not_death():
    b = ReplayBuffer(1, 16, 1, 1, 1, 'cpu')
    add_row(b, 0, start=True)
    add_row(b, 1)
    add_row(b, 2, terminal=True)
    b.end_episode(torch.tensor([0]))
    assert_window(b, [0, 1, 2], [0, 0])


@pytest.mark.parametrize('death', [False, True])
def test_ring_wrap_and_episode_boundary(death):
    b = ReplayBuffer(1, 5, 1, 1, 1, 'cpu')
    # Old episode occupies indices 0..2; new episode spans 3,4,0.
    add_row(b, 10, start=True)
    add_row(b, 11, done=1)
    add_row(b, 12, done=1, terminal=True)
    b.end_episode(torch.tensor([0]))
    add_row(b, 100, start=True)
    add_row(b, 101, done=death)
    add_row(b, 102, done=death, terminal=True)
    b.end_episode(torch.tensor([0]))
    assert b.ptr.item() == 1
    assert_window(b, [100, 101, 102], [0, int(death)])
    # Neither terminal sentinel can be used as an outgoing transition.
    assert not (b.window_mask(1) & b.terminal).any()
    # A longer window would necessarily cross an episode boundary.
    assert not b.window_mask(3).any()
    with pytest.raises(RuntimeError, match='no sampleable rows'):
        b.sample_windows(1, 3)
