import math
import sys
import threading
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import LotteryEngine
from core.backtest import BacktestCancelled, STRATEGY_LAB_STRATEGIES
from core.data import merge_history
from core.data_quality import audit_history, cleaned_unique_draws
from core.history_expansion import depth_status, load_flexible_csv, ranking_windows, scan_import_folder
from core.models import Draw
from core.strategies import smart_ensemble_score, _pair_history_signal
from datetime import date
from core.odds import top_prize_denominator, single_round_top_prize_denominator
from games.registry import ALL_GAMES, BY_KEY
from core.rule_eras import filter_current_analysis_draws, era_summary, rule_profile, current_purchase_rounds
from core.online_updates import (
    SOURCES, fetch_official_results, parse_powerball_html, parse_national_lottery_xml,
    preview_official_update, normalise_existing_for_official_merge,
    RESULT_FEED_KEYS, source_feed_count, source_game_key, OnlineUpdateError,
)


class DrawWiseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LotteryEngine(ROOT)

    def test_all_history_files_load_chronologically(self):
        for cfg in ALL_GAMES:
            draws = self.engine.load(cfg)
            self.assertGreater(len(draws), 0, cfg.key)
            self.assertLessEqual(draws[0].draw_date, draws[-1].draw_date, cfg.key)

    def test_current_game_combination_spaces(self):
        self.assertEqual(single_round_top_prize_denominator(BY_KEY["lotto"]), 45_057_474)
        self.assertTrue(math.isclose(top_prize_denominator(BY_KEY["lotto"]), 22_528_737.247967526, rel_tol=1e-12))
        self.assertEqual(top_prize_denominator(BY_KEY["euromillions"]), 139_838_160)
        self.assertEqual(top_prize_denominator(BY_KEY["set_for_life"]), 15_339_390)
        self.assertEqual(top_prize_denominator(BY_KEY["thunderball"]), 8_060_598)
        self.assertEqual(top_prize_denominator(BY_KEY["powerball"]), 292_201_338)
        self.assertTrue(math.isclose(single_round_top_prize_denominator(BY_KEY["lotto_hotpicks"]), 834_397.6666666666))
        self.assertEqual(current_purchase_rounds(BY_KEY["lotto_hotpicks"]), 2)
        self.assertEqual(top_prize_denominator(BY_KEY["euromillions_hotpicks"]), 2_118_760)

    def test_condensed_generation_all_games(self):
        for cfg in ALL_GAMES:
            result = self.engine.generate(
                config=cfg,
                strategy="Condensed Portfolio",
                lines=8,
                pool_size=cfg.default_pool_size,
                special_pool_size=cfg.default_special_pool_size,
                recent_window=20,
                seed=123,
            )
            self.assertEqual(len(result.tickets), 8, cfg.key)
            self.assertEqual(len(set((t.main, t.special) for t in result.tickets)), 8, cfg.key)
            for t in result.tickets:
                self.assertEqual(len(t.main), cfg.main_pick)
                self.assertEqual(len(set(t.main)), cfg.main_pick)
                self.assertTrue(all(cfg.main_min <= n <= cfg.main_max for n in t.main))
                self.assertEqual(len(t.special), cfg.special_pick)

    def test_diversified_generation_covers_broad_range(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.generate(
            config=cfg,
            strategy="Diversified Smart Portfolio",
            lines=10,
            pool_size=cfg.default_pool_size,
            special_pool_size=0,
            recent_window=20,
            seed=123,
        )
        self.assertEqual(len(result.tickets), 10)
        self.assertGreaterEqual(len(result.base_pool), 40)
        self.assertEqual(result.guarantees, {})

    def test_v50_smart_pick_generates_valid_distinct_lines_for_all_games(self):
        for cfg in ALL_GAMES:
            result = self.engine.generate(
                config=cfg,
                strategy="Smart Pick",
                lines=5,
                pool_size=max(cfg.default_pool_size, cfg.main_pick + 5),
                special_pool_size=cfg.special_range_size if cfg.special_pick else 0,
                recent_window=20,
                seed=321,
            )
            self.assertEqual(len(result.tickets), 5, cfg.key)
            self.assertEqual(len(set((t.main, t.special) for t in result.tickets)), 5, cfg.key)
            for ticket in result.tickets:
                self.assertEqual(len(ticket.main), cfg.main_pick)
                self.assertEqual(len(set(ticket.main)), cfg.main_pick)
                self.assertTrue(all(cfg.main_min <= n <= cfg.main_max for n in ticket.main))
                self.assertEqual(len(ticket.special), cfg.special_pick)
                self.assertTrue(all(cfg.special_min <= n <= cfg.special_max for n in ticket.special) if cfg.special_pick else True)


    def test_v51_smart_ensemble_generates_valid_distinct_lines_for_all_games(self):
        for cfg in ALL_GAMES:
            result = self.engine.generate(
                config=cfg,
                strategy="Smart Ensemble",
                lines=5,
                pool_size=max(cfg.default_pool_size, cfg.main_pick + 5),
                special_pool_size=cfg.special_range_size if cfg.special_pick else 0,
                recent_window=20,
                seed=654,
            )
            self.assertEqual(len(result.tickets), 5, cfg.key)
            self.assertEqual(len(set((t.main, t.special) for t in result.tickets)), 5, cfg.key)
            for ticket in result.tickets:
                self.assertEqual(len(ticket.main), cfg.main_pick)
                self.assertTrue(all(cfg.main_min <= n <= cfg.main_max for n in ticket.main))
                self.assertEqual(len(ticket.special), cfg.special_pick)

    def test_v51_single_pick_is_top_scored_candidate_in_returned_portfolio(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.generate(
            config=cfg,
            strategy="Smart Ensemble",
            lines=5,
            pool_size=14,
            special_pool_size=0,
            recent_window=20,
            seed=777,
        )
        pair_signal = _pair_history_signal(result.draws)
        previous = {tuple(d.main) for d in result.draws}
        scores = [smart_ensemble_score(t.main, cfg, result.main_stats, result.draws, pair_signal, previous) for t in result.tickets]
        self.assertGreaterEqual(scores[0], max(scores[1:]))

    def test_v51_pick_again_exclusion_returns_different_main_line(self):
        cfg = BY_KEY["euromillions"]
        first = self.engine.generate(
            config=cfg,
            strategy="Smart Ensemble",
            lines=1,
            pool_size=14,
            special_pool_size=12,
            recent_window=20,
            seed=101,
        )
        second = self.engine.generate(
            config=cfg,
            strategy="Smart Ensemble",
            lines=1,
            pool_size=14,
            special_pool_size=12,
            recent_window=20,
            seed=101,
            excluded_main_lines={first.tickets[0].main},
        )
        self.assertNotEqual(first.tickets[0].main, second.tickets[0].main)


    def test_key_number_wheel_locks_keys(self):
        cfg = BY_KEY["powerball"]
        result = self.engine.generate(
            config=cfg,
            strategy="Key Number Wheel",
            lines=10,
            pool_size=12,
            special_pool_size=5,
            recent_window=10,
            key_numbers=[7, 34],
        )
        self.assertTrue(all(7 in t.main and 34 in t.main for t in result.tickets))

    def test_full_wheel_includes_special_combinations(self):
        cfg = BY_KEY["euromillions"]
        result = self.engine.generate(
            config=cfg,
            strategy="Full Wheel",
            lines=1,
            pool_size=7,
            special_pool_size=3,
            recent_window=20,
        )
        # C(7,5) main combinations x C(3,2) Lucky Star combinations
        self.assertEqual(len(result.tickets), 63)

    def test_powerball_small_dataset_is_preserved(self):
        cfg = BY_KEY["powerball"]
        draws = self.engine.load(cfg)
        self.assertEqual(len(draws), 10)

    def test_lotto_backtest_runs(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.backtest(
            config=cfg,
            strategy="Condensed Portfolio",
            lines=5,
            pool_size=10,
            special_pool_size=0,
            recent_window=20,
        )
        self.assertGreater(result.tested_draws, 0)
        self.assertTrue(result.threshold_counts)
        self.assertGreaterEqual(result.beat_random_draws, 0)
        self.assertGreaterEqual(result.below_random_draws, 0)

    def test_backtest_reports_progress(self):
        cfg = BY_KEY["lotto"]
        updates = []
        result = self.engine.backtest(
            config=cfg,
            strategy="Condensed Portfolio",
            lines=3,
            pool_size=10,
            special_pool_size=0,
            recent_window=20,
            progress_callback=lambda completed, total, target_date: updates.append((completed, total, target_date)),
        )
        self.assertGreater(result.tested_draws, 0)
        self.assertTrue(updates)
        self.assertEqual(updates[-1][0], updates[-1][1])
        self.assertEqual(updates[-1][0], result.tested_draws)

    def test_backtest_can_be_cancelled(self):
        cfg = BY_KEY["lotto"]
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(BacktestCancelled):
            self.engine.backtest(
                config=cfg,
                strategy="Condensed Portfolio",
                lines=3,
                pool_size=10,
                special_pool_size=0,
                recent_window=20,
                cancel_event=cancel,
            )


    def test_strategy_lab_compares_same_draw_window(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.compare_strategies(
            config=cfg,
            strategies=STRATEGY_LAB_STRATEGIES,
            lines=3,
            pool_size=10,
            special_pool_size=0,
            recent_window=20,
            random_trials_per_draw=3,
        )
        self.assertGreater(result.tested_draws, 0)
        self.assertEqual(len(result.rows), len(STRATEGY_LAB_STRATEGIES))
        self.assertIsNotNone(result.winner)
        self.assertTrue(all(row.tested_draws == result.tested_draws for row in result.rows if not row.error))
        baselines = {round(row.random_avg_best_line_hits, 12) for row in result.rows if not row.error}
        self.assertEqual(len(baselines), 1, "All strategies must use the same shared random benchmark")

    def test_strategy_lab_reports_progress_and_can_cancel(self):
        cfg = BY_KEY["lotto"]
        updates = []
        result = self.engine.compare_strategies(
            config=cfg,
            strategies=("Balanced Random",),
            lines=2,
            pool_size=10,
            special_pool_size=0,
            recent_window=20,
            random_trials_per_draw=2,
            progress_callback=lambda completed, total, phase, strategy, target_date: updates.append(
                (completed, total, phase, strategy, target_date)
            ),
        )
        self.assertTrue(updates)
        self.assertEqual(updates[-1][0], updates[-1][1])
        self.assertEqual(result.tested_draws, len(self.engine.load(cfg)) - 30)

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(BacktestCancelled):
            self.engine.compare_strategies(
                config=cfg,
                strategies=("Balanced Random",),
                lines=2,
                pool_size=10,
                special_pool_size=0,
                recent_window=20,
                random_trials_per_draw=2,
                cancel_event=cancel,
            )


    def test_euromillions_special_ball_analysis_is_separate(self):
        cfg = BY_KEY["euromillions"]
        draws, main_stats, special_stats = self.engine.analyze(cfg, recent_window=20)
        self.assertEqual(len(main_stats), 50)
        self.assertEqual(len(special_stats), 12)
        self.assertEqual({st.number for st in special_stats}, set(range(1, 13)))
        self.assertTrue(all(draw.special for draw in draws))

    def test_special_game_generation_reports_special_coverage(self):
        cfg = BY_KEY["euromillions"]
        result = self.engine.generate(
            config=cfg,
            strategy="Diversified Smart Portfolio",
            lines=10,
            pool_size=cfg.default_pool_size,
            special_pool_size=cfg.default_special_pool_size,
            recent_window=20,
            seed=321,
        )
        self.assertIn("special_coverage", result.metrics)
        self.assertIn("avg_special_overlap", result.metrics)
        self.assertGreater(result.metrics["special_coverage"], 0.0)
        self.assertLessEqual(result.metrics["special_coverage"], 1.0)

    def test_euromillions_backtest_includes_complete_ticket_metrics(self):
        cfg = BY_KEY["euromillions"]
        result = self.engine.backtest(
            config=cfg,
            strategy="Diversified Smart Portfolio",
            lines=5,
            pool_size=cfg.default_pool_size,
            special_pool_size=cfg.default_special_pool_size,
            recent_window=20,
        )
        self.assertGreater(result.tested_draws, 0)
        self.assertGreaterEqual(result.avg_best_total_hits, result.avg_best_line_hits)
        self.assertGreaterEqual(result.random_avg_best_total_hits, result.random_avg_best_line_hits)
        self.assertIn("+", result.best_complete_pattern)
        self.assertTrue(result.pattern_counts)
        self.assertGreater(result.avg_special_coverage, 0.0)
        self.assertLessEqual(result.random_ci_low, result.random_ci_high)
        self.assertLessEqual(result.random_total_ci_low, result.random_total_ci_high)

    def test_strategy_lab_special_game_reports_complete_metrics(self):
        cfg = BY_KEY["euromillions"]
        result = self.engine.compare_strategies(
            config=cfg,
            strategies=("Diversified Smart Portfolio", "Balanced Random"),
            lines=4,
            pool_size=cfg.default_pool_size,
            special_pool_size=cfg.default_special_pool_size,
            recent_window=20,
            random_trials_per_draw=3,
        )
        self.assertGreater(result.random_avg_best_total_hits, 0.0)
        self.assertLessEqual(result.random_ci_low, result.random_ci_high)
        self.assertEqual(len(result.rows), 2)
        for row in result.rows:
            self.assertGreaterEqual(row.avg_best_total_hits, row.avg_best_line_hits)
            self.assertIn("+", row.best_complete_pattern)
            self.assertGreater(row.avg_special_coverage, 0.0)

    def test_concentrated_strategies_generate_distinct_portfolios(self):
        cfg = BY_KEY["euromillions"]
        signatures = []
        for strategy in ("Condensed Portfolio", "Abbreviated Wheel", "Historical Ranked"):
            result = self.engine.generate(
                config=cfg,
                strategy=strategy,
                lines=10,
                pool_size=10,
                special_pool_size=5,
                recent_window=20,
                seed=999,
            )
            signatures.append(frozenset((t.main, t.special) for t in result.tickets))
        self.assertEqual(len(set(signatures)), 3, "These strategies should not collapse into identical portfolios")

    def test_history_merge_adds_and_replaces_by_date(self):
        existing = [
            Draw(date(2026, 1, 1), (1, 2, 3, 4, 5, 6)),
            Draw(date(2026, 1, 8), (7, 8, 9, 10, 11, 12)),
        ]
        incoming = [
            Draw(date(2026, 1, 8), (8, 9, 10, 11, 12, 13)),
            Draw(date(2026, 1, 15), (14, 15, 16, 17, 18, 19)),
        ]
        merged, summary = merge_history(existing, incoming)
        self.assertEqual(len(merged), 3)
        self.assertEqual(summary.new_dates, 1)
        self.assertEqual(summary.replaced_dates, 1)
        self.assertEqual(merged[-1].draw_date, date(2026, 1, 15))

    def test_history_audit_scores_bundled_lotto_data(self):
        cfg = BY_KEY["lotto"]
        audit = audit_history(cfg.csv_path(ROOT), cfg)
        self.assertEqual(audit.invalid_rows, 0)
        self.assertEqual(audit.unique_draw_dates, len(self.engine.load(cfg)))
        self.assertGreaterEqual(audit.quality_score, 80)
        self.assertGreater(audit.completeness_pct, 80.0)

    def test_history_audit_detects_invalid_and_duplicate_rows(self):
        cfg = BY_KEY["lotto"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lotto.csv"
            path.write_text(
                "DrawDate,Ball 1,Ball 2,Ball 3,Ball 4,Ball 5,Ball 6\n"
                "01-Jan-2026,1,2,3,4,5,6\n"
                "01-Jan-2026,7,8,9,10,11,12\n"
                "08-Jan-2026,1,2,3,4,5,99\n",
                encoding="utf-8",
            )
            audit = audit_history(path, cfg)
            self.assertEqual(audit.invalid_rows, 1)
            self.assertEqual(len(audit.duplicate_dates), 1)
            self.assertLess(audit.quality_score, 100)
            self.assertEqual(len(cleaned_unique_draws(audit)), 1)

    def test_analysis_history_windows_slice_only_analysis(self):
        cfg = BY_KEY["lotto"]
        all_draws, _, _ = self.engine.analyze(cfg, recent_window=20)
        last_20, _, _ = self.engine.analyze(cfg, recent_window=20, history_window=20)
        last_50, _, _ = self.engine.analyze(cfg, recent_window=20, history_window=50)
        self.assertGreater(len(all_draws), 50)
        self.assertEqual(len(last_20), 20)
        self.assertEqual(len(last_50), 50)
        self.assertEqual(last_20[-1].draw_date, all_draws[-1].draw_date)

    def test_generation_still_uses_full_history_after_analysis_window_feature(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.generate(
            config=cfg, strategy="Diversified Smart Portfolio", lines=3,
            pool_size=cfg.default_pool_size, special_pool_size=0, recent_window=20, seed=1234
        )
        self.assertEqual(len(result.draws), len(self.engine.load(cfg)))


    def test_merge_preserves_multiple_rounds_on_same_date(self):
        d = date(2026, 8, 12)
        existing = [
            Draw(d, (1, 2, 3, 4, 5, 6), (), "1", "3197"),
            Draw(d, (7, 8, 9, 10, 11, 12), (), "2", "3197"),
        ]
        incoming = [
            Draw(d, (8, 9, 10, 11, 12, 13), (), "2", "3197"),
        ]
        merged, summary = merge_history(existing, incoming)
        self.assertEqual(len(merged), 2)
        self.assertEqual(summary.replaced_records, 1)
        self.assertEqual({draw.round_id for draw in merged}, {"1", "2"})

    def test_lotto_loader_keeps_round_identity(self):
        cfg = BY_KEY["lotto"]
        draws = self.engine.load(cfg)
        same_day = [d for d in draws if d.draw_date == date(2026, 8, 12)]
        self.assertEqual(len(same_day), 2)
        self.assertEqual({d.round_id for d in same_day}, {"1", "2"})
        self.assertEqual({d.draw_number for d in same_day}, {"3197"})


    def test_merge_upgrades_legacy_date_only_record_when_round_identity_arrives(self):
        d = date(2026, 8, 12)
        legacy = [Draw(d, (1, 2, 3, 4, 5, 6))]
        incoming = [
            Draw(d, (1, 2, 3, 4, 5, 6), (), "1", "3197"),
            Draw(d, (7, 8, 9, 10, 11, 12), (), "2", "3197"),
        ]
        merged, summary = merge_history(legacy, incoming)
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(draw.round_id for draw in merged))
        self.assertGreaterEqual(summary.replaced_records, 1)


    def test_v38_flexible_import_accepts_alias_columns(self):
        cfg = BY_KEY["euromillions"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.csv"
            path.write_text(
                "Date,Number1,Number2,Number3,Number4,Number5,Star1,Star2,Draw No\n"
                "01-Aug-2026,1,2,3,4,5,6,7,1234\n",
                encoding="utf-8",
            )
            report = load_flexible_csv(path, cfg)
            self.assertEqual(report.accepted_rows, 1)
            self.assertEqual(report.rejected_rows, 0)
            self.assertEqual(report.draws[0].main, (1, 2, 3, 4, 5))
            self.assertEqual(report.draws[0].special, (6, 7))
            self.assertEqual(report.draws[0].draw_number, "1234")

    def test_v38_flexible_import_reports_bad_rows_without_losing_good_rows(self):
        cfg = BY_KEY["powerball"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "powerball-archive.csv"
            path.write_text(
                "DrawDate,Ball1,Ball2,Ball3,Ball4,Ball5,Power Ball\n"
                "01-Aug-2026,1,2,3,4,5,6\n"
                "02-Aug-2026,1,2,3,4,99,7\n",
                encoding="utf-8",
            )
            report = load_flexible_csv(path, cfg)
            self.assertEqual(report.rows_total, 2)
            self.assertEqual(report.accepted_rows, 1)
            self.assertEqual(report.rejected_rows, 1)
            self.assertTrue(report.issues)

    def test_v38_folder_import_deduplicates_overlapping_archives(self):
        cfg = BY_KEY["euromillions"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            header = "DrawDate,Ball 1,Ball 2,Ball 3,Ball 4,Ball 5,Lucky Star 1,Lucky Star 2\n"
            (root / "a.csv").write_text(header + "01-Aug-2026,1,2,3,4,5,6,7\n", encoding="utf-8")
            (root / "b.csv").write_text(
                header + "01-Aug-2026,1,2,3,4,5,6,7\n04-Aug-2026,8,9,10,11,12,1,2\n",
                encoding="utf-8",
            )
            report = scan_import_folder(root, cfg)
            self.assertEqual(report.files_used, 2)
            self.assertEqual(len(report.draws), 2)
            self.assertEqual(report.identical_overlaps, 1)
            self.assertEqual(report.conflicting_overlaps, 0)

    def test_v38_depth_targets_distinguish_powerball(self):
        euro = depth_status(BY_KEY["euromillions"], 52)
        power = depth_status(BY_KEY["powerball"], 10)
        self.assertEqual(euro.target, 250)
        self.assertEqual(power.target, 500)
        self.assertEqual(euro.tier, "Limited")
        self.assertEqual(power.tier, "Very limited")

    def test_v38_ranking_windows_report_availability_and_overlap(self):
        cfg = BY_KEY["euromillions"]
        draws = self.engine.load(cfg)
        rows = ranking_windows(draws, cfg, recent_window=20, domain="main")
        by_label = {row.label: row for row in rows}
        self.assertTrue(by_label["20"].available)
        self.assertTrue(by_label["50"].available)
        self.assertFalse(by_label["100"].available)
        self.assertEqual(by_label["All"].overlap_with_all, by_label["All"].top_n)

    def test_v38_backtest_horizon_limits_target_draws(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.backtest(
            config=cfg, strategy="Balanced Random", lines=2, pool_size=10,
            special_pool_size=0, recent_window=20, max_tests=25,
        )
        self.assertEqual(result.tested_draws, 25)

    def test_v38_strategy_lab_horizon_limits_target_draws(self):
        cfg = BY_KEY["lotto"]
        result = self.engine.compare_strategies(
            config=cfg, strategies=("Balanced Random",), lines=2, pool_size=10,
            special_pool_size=0, recent_window=20, random_trials_per_draw=2, max_tests=25,
        )
        self.assertEqual(result.tested_draws, 25)
        self.assertEqual(result.rows[0].tested_draws, 25)


    def test_v39_euromillions_rule_era_filters_legacy_stars(self):
        cfg = BY_KEY["euromillions"]
        draws = [
            Draw(date(2016, 9, 23), (1, 2, 3, 4, 5), (1, 2)),
            Draw(date(2016, 9, 27), (6, 7, 8, 9, 10), (11, 12)),
        ]
        current = filter_current_analysis_draws(cfg, draws)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].draw_date, date(2016, 9, 27))
        self.assertIn("2/12 Lucky Stars", rule_profile(cfg).current_analysis_label)

    def test_v39_lotto_59_ball_history_is_analysis_compatible_across_two_round_change(self):
        cfg = BY_KEY["lotto"]
        draws = [
            Draw(date(2015, 10, 7), (1, 2, 3, 4, 5, 6)),
            Draw(date(2015, 10, 10), (1, 2, 3, 4, 5, 59)),
            Draw(date(2026, 6, 10), (7, 8, 9, 10, 11, 12), (), "1", "x"),
            Draw(date(2026, 6, 10), (13, 14, 15, 16, 17, 18), (), "2", "x"),
        ]
        current = filter_current_analysis_draws(cfg, draws)
        self.assertEqual(len(current), 3)
        self.assertEqual(current_purchase_rounds(cfg), 2)

    def test_v39_powerball_current_matrix_start_is_2015_10_07(self):
        cfg = BY_KEY["powerball"]
        profile = rule_profile(cfg)
        self.assertEqual(profile.current_analysis_start, date(2015, 10, 7))
        draws = [
            Draw(date(2015, 10, 3), (1, 2, 3, 4, 5), (6,)),
            Draw(date(2015, 10, 7), (7, 8, 9, 10, 11), (12,)),
        ]
        self.assertEqual(len(filter_current_analysis_draws(cfg, draws)), 1)

    def test_v39_flexible_import_marks_legacy_euromillions_rows_for_review(self):
        cfg = BY_KEY["euromillions"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-em.csv"
            path.write_text(
                "Date,Number1,Number2,Number3,Number4,Number5,Star1,Star2\n"
                "23-Sep-2016,1,2,3,4,5,1,2\n"
                "27-Sep-2016,6,7,8,9,10,11,12\n",
                encoding="utf-8",
            )
            report = load_flexible_csv(path, cfg)
            self.assertEqual(report.accepted_rows, 2)
            self.assertTrue(any(i.severity == "Review" and "outside the current analysis universe" in i.detail for i in report.issues))
            self.assertIn("analysis-ready 1/2", era_summary(cfg, report.draws))

    def test_v39_engine_analysis_uses_rule_compatible_history(self):
        cfg = BY_KEY["euromillions"]
        all_draws = self.engine.load(cfg)
        analysis_draws = self.engine.analysis_draws(cfg)
        self.assertEqual(len(analysis_draws), len(all_draws))
        self.assertTrue(all(d.draw_date >= date(2016, 9, 27) for d in analysis_draws))


    def test_v40_official_sources_are_configured_for_all_games(self):
        self.assertEqual({cfg.key for cfg in ALL_GAMES}, set(SOURCES))
        self.assertTrue(all(SOURCES[cfg.key].page_url.startswith("https://") for cfg in ALL_GAMES))

    def test_v40_parses_official_powerball_previous_results_shape(self):
        raw = b"""
        <html><body>
        <a>Sat, Aug 15, 2026 5 8 27 29 63 13 Power Play 2x</a>
        <a>Wed, Aug 12, 2026 4 26 66 67 69 9 Power Play 2x</a>
        </body></html>
        """
        draws = parse_powerball_html(raw)
        self.assertEqual(len(draws), 2)
        self.assertEqual(draws[-1].draw_date, date(2026, 8, 15))
        self.assertEqual(draws[0].main, (4, 26, 66, 67, 69))
        self.assertEqual(draws[0].special, (9,))

    def test_v40_fetches_and_maps_national_lottery_csv_without_live_network(self):
        cfg = BY_KEY["euromillions"]
        csv_bytes = (
            "DrawDate,Ball 1,Ball 2,Ball 3,Ball 4,Ball 5,Lucky Star 1,Lucky Star 2,DrawNumber\n"
            "14-Aug-2026,5,29,39,48,49,4,8,1972\n"
        ).encode("utf-8")
        result = fetch_official_results(
            cfg, registry=BY_KEY, fetcher=lambda _url, _timeout: csv_bytes, cache={}
        )
        self.assertEqual(len(result.draws), 1)
        self.assertEqual(result.draws[0].main, (5, 29, 39, 48, 49))
        self.assertEqual(result.draws[0].special, (4, 8))

    def test_v40_euromillions_hotpicks_projects_parent_draw_without_lucky_stars(self):
        cfg = BY_KEY["euromillions_hotpicks"]
        csv_bytes = (
            "DrawDate,Ball 1,Ball 2,Ball 3,Ball 4,Ball 5,Lucky Star 1,Lucky Star 2,DrawNumber\n"
            "14-Aug-2026,5,29,39,48,49,4,8,1972\n"
        ).encode("utf-8")
        result = fetch_official_results(
            cfg, registry=BY_KEY, fetcher=lambda _url, _timeout: csv_bytes, cache={}
        )
        self.assertEqual(result.draws[0].main, (5, 29, 39, 48, 49))
        self.assertEqual(result.draws[0].special, ())

    def test_v40_lotto_hotpicks_flexible_import_keeps_all_six_draw_balls(self):
        cfg = BY_KEY["lotto_hotpicks"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lotto-source.csv"
            path.write_text(
                "DrawDate,Ball 1,Ball 2,Ball 3,Ball 4,Ball 5,Ball 6,DrawNumber\n"
                "12-Aug-2026,3,14,22,24,34,54,3197\n",
                encoding="utf-8",
            )
            report = load_flexible_csv(path, cfg)
            self.assertEqual(report.accepted_rows, 1)
            self.assertEqual(report.draws[0].main, (3, 14, 22, 24, 34, 54))

    def test_v40_powerball_official_date_normalises_legacy_uk_shift(self):
        cfg = BY_KEY["powerball"]
        local = [Draw(date(2026, 8, 13), (4, 26, 66, 67, 69), (9,))]
        incoming = (Draw(date(2026, 8, 12), (4, 26, 66, 67, 69), (9,)),)
        cleaned, count = normalise_existing_for_official_merge(cfg, local, incoming)
        self.assertEqual(count, 1)
        self.assertEqual(cleaned, [])

    def test_v40_update_preview_is_non_destructive_and_counts_new_rows(self):
        cfg = BY_KEY["powerball"]
        local = [Draw(date(2026, 8, 12), (4, 26, 66, 67, 69), (9,))]
        raw = b"<a>Sat, Aug 15, 2026 5 8 27 29 63 13 Power Play 2x</a><a>Wed, Aug 12, 2026 4 26 66 67 69 9 Power Play 2x</a>"
        result = fetch_official_results(
            cfg, registry=BY_KEY, fetcher=lambda _url, _timeout: raw, cache={}
        )
        preview = preview_official_update(cfg, local, result)
        self.assertEqual(preview.new_records, 1)
        self.assertEqual(preview.final_rows, 2)
        self.assertEqual(len(local), 1, "Preview must not mutate local history")
        self.assertEqual(preview.status, "Update available")


    def test_v41_uses_five_physical_result_feeds_for_seven_games(self):
        self.assertEqual(source_feed_count(), 5)
        self.assertEqual(len(RESULT_FEED_KEYS), 5)
        self.assertEqual(source_game_key("lotto_hotpicks"), "lotto")
        self.assertEqual(source_game_key("euromillions_hotpicks"), "euromillions")
        self.assertEqual({cfg.key for cfg in ALL_GAMES}, set(SOURCES))

    def test_v41_flexible_csv_accepts_semicolon_and_tab_delimiters(self):
        cfg = BY_KEY["euromillions"]
        with tempfile.TemporaryDirectory() as tmp:
            semicolon = Path(tmp) / "em_semicolon.csv"
            semicolon.write_text(
                "Draw Date;Main Ball 1;Main Ball 2;Main Ball 3;Main Ball 4;Main Ball 5;Star 1;Star 2\n"
                "14-Aug-2026;5;29;39;48;49;4;8\n", encoding="utf-8"
            )
            report = load_flexible_csv(semicolon, cfg)
            self.assertEqual(report.accepted_rows, 1)
            self.assertIn("delimiter=';'", report.mapping_summary)

            tabbed = Path(tmp) / "em_tab.tsv"
            tabbed.write_text(
                "DrawDate\tNumber1\tNumber2\tNumber3\tNumber4\tNumber5\tLucky Star 1\tLucky Star 2\n"
                "14-Aug-2026\t5\t29\t39\t48\t49\t4\t8\n", encoding="utf-8"
            )
            report2 = load_flexible_csv(tabbed, cfg)
            self.assertEqual(report2.accepted_rows, 1)
            self.assertIn("delimiter='\\t'", report2.mapping_summary)

    def test_v41_parses_national_lottery_xml_with_number_sets(self):
        cfg = BY_KEY["euromillions"]
        raw = b'''<?xml version="1.0"?>
        <draw-history><result><draw><draw-date>2026-08-14</draw-date><draw-number>1972</draw-number></draw>
        <balls><set type="main"><ball>5</ball><ball>29</ball><ball>39</ball><ball>48</ball><ball>49</ball></set>
        <set type="lucky-stars"><ball>4</ball><ball>8</ball></set></balls></result></draw-history>'''
        draws = parse_national_lottery_xml(raw, cfg)
        self.assertEqual(len(draws), 1)
        self.assertEqual(draws[0].main, (5, 29, 39, 48, 49))
        self.assertEqual(draws[0].special, (4, 8))
        self.assertEqual(draws[0].draw_number, "1972")

    def test_v41_parses_two_round_lotto_xml_without_bonus_pollution(self):
        cfg = BY_KEY["lotto"]
        raw = b'''<draw-history>
        <result><draw><draw-date>2026-08-15</draw-date><draw-number>3198</draw-number><round>1</round></draw>
        <balls><set type="main"><ball>17</ball><ball>27</ball><ball>28</ball><ball>42</ball><ball>47</ball><ball>59</ball><ball>7</ball></set></balls></result>
        <result><draw><draw-date>2026-08-15</draw-date><draw-number>3198</draw-number><round>2</round></draw>
        <balls><set type="main"><ball>4</ball><ball>18</ball><ball>22</ball><ball>30</ball><ball>32</ball><ball>45</ball><ball>55</ball></set></balls></result>
        </draw-history>'''
        draws = parse_national_lottery_xml(raw, cfg)
        self.assertEqual(len(draws), 2)
        self.assertEqual(draws[0].round_id, "1")
        self.assertEqual(draws[0].main, (17, 27, 28, 42, 47, 59))
        self.assertEqual(draws[1].round_id, "2")
        self.assertEqual(draws[1].main, (4, 18, 22, 30, 32, 45))

    def test_v41_falls_back_from_html_response_to_alternate_official_feed(self):
        cfg = BY_KEY["euromillions"]
        csv_bytes = (
            "DrawDate,Ball 1,Ball 2,Ball 3,Ball 4,Ball 5,Lucky Star 1,Lucky Star 2,DrawNumber\n"
            "14-Aug-2026,5,29,39,48,49,4,8,1972\n"
        ).encode("utf-8")
        calls = []
        def fetcher(url, _timeout):
            calls.append(url)
            return b"<html><body>not the machine-readable feed</body></html>" if url.endswith("/xml") else csv_bytes
        result = fetch_official_results(cfg, registry=BY_KEY, fetcher=fetcher, cache={})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.parser_used, "National Lottery CSV")
        self.assertEqual(result.draws[0].special, (4, 8))

    def test_v41_parse_failure_preserves_raw_diagnostic(self):
        cfg = BY_KEY["euromillions"]
        raw = b"<html><body>unexpected landing page</body></html>"
        with self.assertRaises(OnlineUpdateError) as cm:
            fetch_official_results(cfg, registry=BY_KEY, fetcher=lambda _url, _timeout: raw, cache={})
        exc = cm.exception
        self.assertEqual(exc.diagnostic_bytes, raw)
        self.assertEqual(exc.diagnostic_extension, ".html")
        self.assertIn("no local history was modified", str(exc).lower())


    def test_v533_excel_history_import(self):
        from openpyxl import Workbook
        cfg = BY_KEY["euromillions"]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "euromillions-results.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.append(["DrawDate", "Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5", "Lucky Star 1", "Lucky Star 2"])
            ws.append([date(2026, 8, 14), 5, 29, 39, 48, 49, 4, 8])
            wb.save(path)
            report = load_flexible_csv(path, cfg)
            self.assertEqual(report.accepted_rows, 1)
            self.assertEqual(report.draws[0].main, (5, 29, 39, 48, 49))
            self.assertEqual(report.draws[0].special, (4, 8))


if __name__ == "__main__":
    unittest.main()
