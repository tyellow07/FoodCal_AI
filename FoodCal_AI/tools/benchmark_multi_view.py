"""Compare single-photo and two-photo (top + side) volume estimates on synthetic camera scenes.

    python -B -m tools.benchmark_multi_view [--scenes 200] [--seed 7] [--exact]

Each scene is a heap, slab or bowl of known size photographed by a pinhole camera from above and from the side
(src/evaluation/multi_view_sim.py), with Gemini-like box errors and card-finder errors unless --exact. Prints a
Markdown table of the middle value's error (MAPE, median), how often the true volume falls inside the stated range,
and the range's width (high / low). Synthetic only: it checks geometry and measurement tolerance, not real foods.
"""
import argparse

from src.evaluation.multi_view_sim import benchmark

NAMES = {'heap': '堆狀（白飯）', 'slab': '片狀（雞排、蛋）', 'bowl': '碗裝'}


def table(scenes, seed, exact):
    noise = {'box_noise': 0.0, 'card_scale_noise': 0.0, 'card_angle_noise_deg': 0.0} if exact else {}
    lines = ['| 形狀 | 單張 MAPE | 加側面照 MAPE | 加側面照中位誤差 | 涵蓋率 單張 → 加側面照 | 範圍寬度（高/低）加側面照 |',
             '| --- | --- | --- | --- | --- | --- |']
    for kind in ('heap', 'slab', 'bowl'):
        r = benchmark(kind, n=scenes, seed=seed, **noise)
        single, multi = r['single'], r['multi']
        width = '含 0' if multi['median_width'] == float('inf') else f"{multi['median_width']:.2f}"
        lines.append(f"| {NAMES[kind]} | {single['mape']:.0%} | {multi['mape']:.0%} | {multi['median_ape']:.0%} | "
                     f"{single['coverage']:.0%} → {multi['coverage']:.0%} | {width} |")
        if r['two_photo_failed']:
            lines.append(f"|  | （{r['two_photo_failed']} 個場景量不出來） | | | | |")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--scenes', type=int, default=200)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--exact', action='store_true', help='no box or card errors')
    args = parser.parse_args(argv)
    print(table(args.scenes, args.seed, args.exact))


if __name__ == '__main__':
    main()
