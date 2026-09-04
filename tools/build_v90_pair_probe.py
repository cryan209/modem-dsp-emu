"""Build fresh diagnostic bridges against a local v90modem checkout."""
import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sibling', type=Path,
                        default=Path('/Users/scottcryan/v90modem'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / 'artifacts/v90-reliability-investigation'
    output.mkdir(parents=True, exist_ok=True)
    sources = ('v90 v90_cp_rx v90_cp_live v90_dil_measure v90_dil_presets '
               'v90_analogue_tx v90_analogue_rx v90_analogue_phase3 '
               'v90_analogue_phase4 v91 vpcm_cp v92_phase4_decode p3_demod')
    for role, source in [('a', 'v90a_phase3_bridge_probe.c'),
                         ('d', 'v90_digital_phase3_event_bridge.c')]:
        command = ['clang', '-O2', '-I' + str(args.sibling),
                   '-I' + str(args.sibling / 'spandsp-master/src'),
                   '-I/opt/homebrew/include', str(root / 'tools' / source)]
        command += [str(args.sibling / (name + '.c')) for name in sources.split()]
        command += [str(args.sibling / 'spandsp-master/src/.libs/libspandsp.a'),
                    '-L/opt/homebrew/lib', '-ltiff', '-ljpeg', '-lm', '-lpthread',
                    '-o', str(output / ('bridge-' + role))]
        subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
