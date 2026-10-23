"""The large exam: many more mined cases than tests/regression, kept apart so the unit tests stay fast.

    python tools/exam/large.py mine --per-tag 60              # mine into vakuu/button-runtime/exam-large/
    python tools/exam/large.py run --config claude:haiku@low --tags energy tempo potion kill potion_lethal

Takes the same arguments as mine_exam.py and regression.py. Cases already in tests/regression are
left out, so the two sets never overlap.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import regression  # noqa: E402

LARGE = Path(__file__).resolve().parents[2] / 'vakuu' / 'button-runtime' / 'exam-large'


def main():
    command, rest = sys.argv[1], sys.argv[2:]
    LARGE.mkdir(parents=True, exist_ok=True)
    if command == 'mine':
        import mine_exam
        main_suite = {c['id'] for c in regression.load()}
        regression.SUITE = LARGE
        original_load = regression.load
        # mine_exam skips what the suite already holds; include the main suite so nothing is duplicated.
        regression.load = lambda: original_load() + [{'id': i} for i in main_suite]
        sys.argv = ['mine_exam.py', *rest]
        mine_exam.main()
    else:
        regression.SUITE = LARGE
        sys.argv = ['regression.py', command, *rest]
        regression.main()


if __name__ == '__main__': main()
