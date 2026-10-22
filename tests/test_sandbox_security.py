
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="RLIMIT_NPROC containment is Linux-specific.",
)
def test_fork_bomb_is_contained():
    box = Sandbox(
        policy=SandboxPolicy(
            limits=ResourceLimits(
                wall_time_seconds=6,
                max_processes=16,
            )
        )
    )

    box.write_file(
        "fork.py",
        "import os, sys\n"
        "for _ in range(10000):\n"
        "    try:\n"
        "        os.fork()\n"
        "    except OSError as exc:\n"
        "        print(f'OSError: {exc}', file=sys.stderr)\n"
        "        sys.exit(1)\n",
    )

    try:
        result = box.run(["python", "fork.py"])
    finally:
        box.cleanup()

    assert (
        result.timed_out
        or result.exit_code != 0
        or "OSError" in result.stderr
    ), "Fork bomb completed without triggering a containment condition"
