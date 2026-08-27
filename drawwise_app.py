from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_paths import APP_NAME, APP_VERSION, ensure_runtime_layout, reports_root, resource_root, user_root

RESOURCE_ROOT = resource_root()
if str(RESOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(RESOURCE_ROOT))

from core.data import load_draws, merge_history, write_draws
from core.data_quality import audit_history, cleaned_unique_draws
from core.history_expansion import (
    depth_status, load_flexible_csv, ranking_windows, scan_import_folder,
)
from core.engine import LotteryEngine
from core.backtest import BacktestCancelled, STRATEGY_LAB_STRATEGIES
from core.odds import format_odds, single_round_top_prize_denominator
from core.strategies import STRATEGIES
from core.online_updates import (
    OnlineUpdateError, fetch_official_results, official_source, preview_official_update,
    merge_official_update, RESULT_FEED_KEYS, DERIVED_CHILDREN, source_feed_count,
    source_game_key, derive_result_for_game,
)
from core.rule_eras import (
    era_detail_lines, era_summary, filter_current_analysis_draws, rule_profile, current_purchase_rounds,
)
from games.registry import ALL_GAMES, BY_KEY, BY_NAME


PALETTE = {
    # CVMatchPro-inspired accessible palette: deep navy navigation, bright blue focus,
    # white work surfaces and restrained secondary accents.
    "bg": "#F4F7FB",
    "surface": "#FFFFFF",
    "sidebar": "#0B1B2F",
    "sidebar_card": "#102844",
    "sidebar_card_alt": "#132D4B",
    "sidebar_border": "#29496A",
    "sidebar_text": "#F8FAFC",
    "sidebar_muted": "#B8C7DA",
    "blue": "#3B82F6",
    "blue_bright": "#58A6FF",
    "cyan": "#22D3EE",
    "green": "#22C55E",
    "orange": "#F97316",
    "purple": "#8B5CF6",
    "gold": "#FACC15",
    "ink": "#0F172A",
    "soft_ink": "#475569",
    "line": "#D5DEE9",
    "soft_blue": "#EAF3FF",
    "disabled_bg": "#E2E8F0",
    "disabled_fg": "#64748B",
    "success_bg": "#DCFCE7",
    "success_fg": "#166534",
}


STRATEGY_HELP = {
    "Smart Ensemble": (
        "Personal V5.1 default. Combines multiple historical views, recent activity, pair signals, structural balance and low-overlap portfolio selection with automatic small-sample shrinkage."
    ),
    "Smart Pick": (
        "Legacy V5.0 alias retained for compatibility; it now uses the Smart Ensemble engine."
    ),
    "Diversified Smart Portfolio": (
        "Best default for broad coverage. Uses the full number universe, a soft historical tilt, "
        "and aggressively reduces overlap between your own lines."
    ),
    "Condensed Portfolio": (
        "Builds a ranked base pool, then chooses a smaller set of lines that maximises pair/triple coverage."
    ),
    "Abbreviated Wheel": (
        "Coverage-first wheel inside a chosen base pool. Pair coverage dominates, historical scoring is only a small tie-break, and exact conditional guarantees are calculated."
    ),
    "Historical Ranked": (
        "Concentration-first method. Uses all-history frequency, recent frequency and a small gap component, then takes the highest-scoring lines with minimal coverage intervention."
    ),
    "Hot + Cold Blend": (
        "Builds the base pool from a mix of high-ranked and low-ranked historical numbers, then structures the tickets."
    ),
    "Key Number Wheel": (
        "Locks your chosen key number(s) into every main-number line and wheels the remaining positions."
    ),
    "Balanced Random": (
        "Distinct random lines with light crowd-pattern avoidance. Useful as a clean baseline against historical methods."
    ),
    "Full Wheel": (
        "Generates every complete combination inside the selected base pool. Coverage is complete, but cost rises very quickly."
    ),
}

POOL_STRATEGIES = {
    "Condensed Portfolio",
    "Abbreviated Wheel",
    "Historical Ranked",
    "Hot + Cold Blend",
    "Key Number Wheel",
    "Full Wheel",
}
SPECIAL_POOL_STRATEGIES = POOL_STRATEGIES


GAME_ACCENTS = {
    "lotto": "#2563EB",
    "lotto_hotpicks": "#0E7490",
    "euromillions": "#D97706",
    "euromillions_hotpicks": "#7C3AED",
    "set_for_life": "#15803D",
    "thunderball": "#EA580C",
    "powerball": "#DC2626",
}

SPECIAL_STYLES = {
    "Powerball": ("#DC2626", "#FFFFFF"),
    "Lucky Stars": ("#FACC15", "#111827"),
    "Lucky Star": ("#FACC15", "#111827"),
    "Life Ball": ("#7C3AED", "#FFFFFF"),
    "Thunderball": ("#EA580C", "#FFFFFF"),
}


class DrawWiseApp(tk.Tk):
    def __init__(self, start_game: str | None = None):
        super().__init__()
        self.runtime_root = ensure_runtime_layout()
        self.engine = LotteryEngine(self.runtime_root)
        self.last_result = None
        self.last_ticket_report = ""
        self._backtest_thread = None
        self._backtest_cancel = threading.Event()
        self._backtest_queue = queue.Queue()
        self._backtest_started_at = None
        self._strategy_lab_thread = None
        self._strategy_lab_cancel = threading.Event()
        self._strategy_lab_queue = queue.Queue()
        self._strategy_lab_started_at = None
        self._strategy_lab_last_result = None
        self._updates_thread = None
        self._updates_queue = queue.Queue()
        self._online_results = {}
        self._online_previews = {}
        self._online_errors = {}
        self._online_source_errors = {}
        self._online_diagnostics = {}

        self.title(f"{APP_NAME} {APP_VERSION} — Number Strategy & Wheeling")
        self.geometry("1420x900")
        self.minsize(1160, 720)
        self.configure(bg=PALETTE["bg"])

        icon = RESOURCE_ROOT / "assets" / "drawwise.ico"
        if icon.exists() and sys.platform.startswith("win"):
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

        self._build_style()
        self._build_ui()

        if start_game and start_game in BY_KEY:
            self.game_var.set(BY_KEY[start_game].name)
        else:
            self.game_var.set(ALL_GAMES[0].name)
        self.on_game_changed()

    # ---------- styling ----------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "DrawWise.TCombobox",
            fieldbackground=PALETTE["surface"],
            background=PALETTE["surface"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            lightcolor=PALETTE["line"],
            darkcolor=PALETTE["line"],
            arrowcolor=PALETTE["ink"],
            padding=8,
        )
        style.map(
            "DrawWise.TCombobox",
            fieldbackground=[("readonly", PALETTE["surface"])],
            foreground=[("readonly", PALETTE["ink"])],
            selectbackground=[("readonly", PALETTE["surface"])],
            selectforeground=[("readonly", PALETTE["ink"])],
            bordercolor=[("focus", PALETTE["blue_bright"])],
            lightcolor=[("focus", PALETTE["blue_bright"])],
            darkcolor=[("focus", PALETTE["blue_bright"])],
        )

        style.configure(
            "DrawWise.TSpinbox",
            fieldbackground=PALETTE["surface"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            arrowcolor=PALETTE["ink"],
            padding=6,
        )
        style.map(
            "DrawWise.TSpinbox",
            bordercolor=[("focus", PALETTE["blue_bright"])],
            lightcolor=[("focus", PALETTE["blue_bright"])],
            darkcolor=[("focus", PALETTE["blue_bright"])],
        )

        style.configure("DrawWise.TNotebook", background=PALETTE["bg"], borderwidth=0)
        style.configure(
            "DrawWise.TNotebook.Tab",
            background="#E9EEF5",
            foreground=PALETTE["soft_ink"],
            font=("Segoe UI", 10, "bold"),
            padding=(18, 11),
            borderwidth=0,
        )
        style.map(
            "DrawWise.TNotebook.Tab",
            background=[("selected", PALETTE["surface"]), ("active", PALETTE["soft_blue"])],
            foreground=[("selected", PALETTE["blue"]), ("active", PALETTE["ink"])],
        )
        style.configure(
            "DrawWise.Vertical.TScrollbar",
            troughcolor="#E7EDF4",
            background="#9BAABD",
            bordercolor="#E7EDF4",
            arrowcolor=PALETTE["ink"],
        )
        style.configure(
            "Backtest.Horizontal.TProgressbar",
            troughcolor="#E7EDF4",
            background="#2563EB",
            bordercolor="#D5DEE9",
            lightcolor="#2563EB",
            darkcolor="#2563EB",
            thickness=14,
        )
        style.configure(
            "Analysis.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground=PALETTE["ink"],
            rowheight=31,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Analysis.Treeview.Heading",
            background="#EAF1F9",
            foreground=PALETTE["ink"],
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padding=7,
        )
        style.map(
            "Analysis.Treeview",
            background=[("selected", "#DCEBFF")],
            foreground=[("selected", PALETTE["ink"])],
        )

    def _focusable_button(self, parent, text, command, *, bg, fg="white", subtle=False):
        """Accessible button with a visible keyboard focus ring and a generous target."""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=PALETTE["blue_bright"] if not subtle else PALETTE["sidebar_card_alt"],
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            takefocus=True,
            highlightthickness=2,
            highlightbackground=bg,
            highlightcolor=PALETTE["blue_bright"],
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=10,
        )

    def _nav_heading(self, parent, icon, text, accent):
        row = tk.Frame(parent, bg=PALETTE["sidebar"])
        badge = tk.Label(
            row,
            text=icon,
            width=2,
            bg=PALETTE["sidebar_card_alt"],
            fg=accent,
            font=("Segoe UI Symbol", 12, "bold"),
            padx=4,
            pady=3,
        )
        badge.pack(side="left")
        tk.Label(
            row,
            text=text,
            bg=PALETTE["sidebar"],
            fg=PALETTE["sidebar_text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(10, 0))
        return row

    def _card(self, parent, *, bg=None, border=None):
        return tk.Frame(
            parent,
            bg=bg or PALETTE["sidebar_card"],
            highlightthickness=1,
            highlightbackground=border or PALETTE["sidebar_border"],
        )

    # ---------- UI ----------
    def _build_ui(self):
        # No large banner. The application uses the same visual language as CVMatchPro:
        # a deep navy navigation/control rail and a clean light workspace.
        content = tk.Frame(self, bg=PALETTE["bg"])
        content.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(content, bg=PALETTE["sidebar"], width=405)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        main = tk.Frame(content, bg=PALETTE["bg"])
        main.pack(side="left", fill="both", expand=True, padx=20, pady=18)

        self.game_var = tk.StringVar()
        self.strategy_var = tk.StringVar(value="Diversified Smart Portfolio")
        self.lines_var = tk.IntVar(value=10)
        self.pool_var = tk.IntVar(value=10)
        self.special_pool_var = tk.IntVar(value=4)
        self.recent_var = tk.IntVar(value=20)
        self.analysis_history_window_var = tk.StringVar(value="All")
        self.key_var = tk.StringVar()
        self.data_status_var = tk.StringVar()
        self.strategy_help_var = tk.StringVar()
        self.control_hint_var = tk.StringVar()
        self.footer_var = tk.StringVar(value="Ready")

        self._build_sidebar()
        self._build_main(main)
        self._bind_shortcuts()

    def _build_sidebar(self):
        s = self.sidebar
        padx = 18

        # Brand block, modelled on the compact CVMatchPro product header.
        brand = tk.Frame(s, bg=PALETTE["sidebar"])
        brand.pack(fill="x", padx=padx, pady=(18, 14))
        logo = tk.Label(
            brand,
            text="DW",
            width=3,
            bg="#174EA6",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            padx=5,
            pady=9,
        )
        logo.pack(side="left")
        brand_text = tk.Frame(brand, bg=PALETTE["sidebar"])
        brand_text.pack(side="left", padx=(12, 0))
        tk.Label(
            brand_text, text="DrawWise", bg=PALETTE["sidebar"], fg="white",
            font=("Segoe UI", 18, "bold")
        ).pack(anchor="w")
        tk.Label(
            brand_text, text="NUMBER STRATEGY & WHEELING", bg=PALETTE["sidebar"],
            fg=PALETTE["sidebar_muted"], font=("Segoe UI", 8, "bold")
        ).pack(anchor="w")

        # Workspace marker similar to the highlighted CVMatchPro sidebar item.
        workspace = self._card(s, bg="#102844", border=PALETTE["blue_bright"])
        workspace.pack(fill="x", padx=padx, pady=(0, 16))
        tk.Frame(workspace, width=4, bg=PALETTE["blue_bright"]).pack(side="left", fill="y")
        tk.Label(
            workspace, text="Lottery workspace", bg="#102844", fg="white",
            font=("Segoe UI", 11, "bold"), padx=12, pady=12
        ).pack(side="left")
        tk.Label(
            workspace, text="LOCAL", bg="#174EA6", fg="white",
            font=("Segoe UI", 7, "bold"), padx=7, pady=3
        ).pack(side="right", padx=10)

        self._nav_heading(s, "◎", "Choose game", PALETTE["blue_bright"]).pack(fill="x", padx=padx)
        self.game_combo = ttk.Combobox(
            s, textvariable=self.game_var, values=[g.name for g in ALL_GAMES],
            state="readonly", style="DrawWise.TCombobox", font=("Segoe UI", 11), takefocus=True
        )
        self.game_combo.pack(fill="x", padx=padx, pady=(7, 15), ipady=2)
        self.game_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_game_changed())

        self._nav_heading(s, "◇", "Selection strategy", PALETTE["purple"]).pack(fill="x", padx=padx)
        self.strategy_combo = ttk.Combobox(
            s, textvariable=self.strategy_var, values=STRATEGIES,
            state="readonly", style="DrawWise.TCombobox", font=("Segoe UI", 11), takefocus=True
        )
        self.strategy_combo.pack(fill="x", padx=padx, pady=(7, 6), ipady=2)
        self.strategy_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_strategy_changed())
        tk.Label(
            s, textvariable=self.strategy_help_var, wraplength=350, justify="left",
            bg=PALETTE["sidebar"], fg="#D5E1F0", font=("Segoe UI", 10)
        ).pack(fill="x", padx=padx, pady=(0, 12))

        # Selection settings: only controls relevant to the selected strategy are shown.
        settings = self._card(s)
        settings.pack(fill="x", padx=padx, pady=(0, 12))
        settings_header = tk.Frame(settings, bg=PALETTE["sidebar_card"])
        settings_header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(
            settings_header, text="⚙", bg=PALETTE["sidebar_card"], fg=PALETTE["green"],
            font=("Segoe UI Symbol", 11, "bold")
        ).pack(side="left")
        tk.Label(
            settings_header, text="Selection settings", bg=PALETTE["sidebar_card"],
            fg="white", font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=(8, 0))

        self.settings_body = tk.Frame(settings, bg=PALETTE["sidebar_card"])
        self.settings_body.pack(fill="x", padx=12, pady=(0, 6))

        def setting_row(label, variable, from_, to):
            row = tk.Frame(self.settings_body, bg=PALETTE["sidebar_card"])
            tk.Label(
                row, text=label, bg=PALETTE["sidebar_card"], fg=PALETTE["sidebar_text"],
                font=("Segoe UI", 9)
            ).pack(side="left")
            spin = ttk.Spinbox(
                row, from_=from_, to=to, textvariable=variable, width=8,
                style="DrawWise.TSpinbox", font=("Segoe UI", 10), takefocus=True
            )
            spin.pack(side="right")
            return row, spin

        self.lines_row, self.lines_spin = setting_row("Ticket lines", self.lines_var, 1, 100)
        self.pool_row, self.pool_spin = setting_row("Main base pool", self.pool_var, 5, 18)
        self.special_row, self.special_spin = setting_row("Special-ball pool", self.special_pool_var, 0, 12)
        self.recent_row, self.recent_spin = setting_row("Recent draw window", self.recent_var, 5, 100)

        self.key_row = tk.Frame(self.settings_body, bg=PALETTE["sidebar_card"])
        tk.Label(
            self.key_row, text="Key numbers", bg=PALETTE["sidebar_card"], fg=PALETTE["sidebar_text"],
            font=("Segoe UI", 9)
        ).pack(side="left")
        self.key_entry = tk.Entry(
            self.key_row, textvariable=self.key_var, width=13, bg="white", fg=PALETTE["ink"],
            disabledbackground=PALETTE["disabled_bg"], disabledforeground=PALETTE["disabled_fg"],
            relief="flat", font=("Segoe UI", 10), takefocus=True,
            highlightthickness=2, highlightbackground=PALETTE["line"], highlightcolor=PALETTE["blue_bright"]
        )
        self.key_entry.pack(side="right", ipady=6)

        for row in (self.lines_row, self.pool_row, self.special_row, self.recent_row, self.key_row):
            row.pack(fill="x", pady=5)

        tk.Label(
            settings, textvariable=self.control_hint_var, bg=PALETTE["sidebar_card"],
            fg="#D5E1F0", wraplength=350, justify="left", font=("Segoe UI", 9)
        ).pack(fill="x", padx=12, pady=(0, 10))

        # History card with a high-contrast update action.
        data_card = self._card(s, bg="#0F2D42", border="#23577C")
        data_card.pack(fill="x", padx=padx, pady=(0, 12))
        data_head = tk.Frame(data_card, bg="#0F2D42")
        data_head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(
            data_head, text="▣", bg="#0F2D42", fg=PALETTE["cyan"],
            font=("Segoe UI Symbol", 12, "bold")
        ).pack(side="left")
        tk.Label(
            data_head, text="History data", bg="#0F2D42", fg="white",
            font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            data_card, textvariable=self.data_status_var, bg="#0F2D42", fg="#E6F4FF",
            wraplength=350, justify="left", font=("Segoe UI", 10)
        ).pack(fill="x", padx=12, pady=(0, 9))
        smalls = tk.Frame(data_card, bg="#0F2D42")
        smalls.pack(fill="x", padx=12, pady=(0, 11))
        self._focusable_button(
            smalls, "Check new results", self.open_updater_and_check_selected, bg="#087EA4"
        ).pack(side="left", fill="x", expand=True)
        self._focusable_button(
            smalls, "Open folder", self.open_data_folder, bg="#294562", subtle=True
        ).pack(side="left", padx=(7, 0))

        # Primary actions: single dominant blue action, then secondary actions.
        self.generate_button = self._focusable_button(
            s, "Generate tickets", self.generate, bg="#2563EB"
        )
        self.generate_button.pack(fill="x", padx=padx, pady=(0, 7))

        actions = tk.Frame(s, bg=PALETTE["sidebar"])
        actions.pack(fill="x", padx=padx)
        self._focusable_button(actions, "Analyse", self.analyse, bg="#0E7490").pack(
            side="left", fill="x", expand=True
        )
        self._focusable_button(actions, "Backtest", self.backtest, bg="#B45309").pack(
            side="left", fill="x", expand=True, padx=(7, 0)
        )
        self._focusable_button(
            s, "Save generated tickets", self.save_tickets, bg="#15803D"
        ).pack(fill="x", padx=padx, pady=(7, 0))

        tk.Label(
            s, text=f"{APP_NAME} {APP_VERSION}  •  Local desktop data",
            bg=PALETTE["sidebar"], fg=PALETTE["sidebar_muted"], font=("Segoe UI", 8)
        ).pack(side="bottom", anchor="w", padx=padx, pady=12)

    def _build_main(self, parent):
        head = tk.Frame(parent, bg=PALETTE["bg"])
        head.pack(fill="x", pady=(0, 12))
        title_area = tk.Frame(head, bg=PALETTE["bg"])
        title_area.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_area, text="Lottery workspace", bg=PALETTE["bg"], fg=PALETTE["ink"],
            font=("Segoe UI", 21, "bold")
        ).pack(anchor="w")
        tk.Label(
            title_area,
            text="Build diversified ticket portfolios, inspect draw history and test methods out of sample.",
            bg=PALETTE["bg"], fg=PALETTE["soft_ink"], font=("Segoe UI", 10)
        ).pack(anchor="w", pady=(3, 0))

        chips = tk.Frame(head, bg=PALETTE["bg"])
        chips.pack(side="right", anchor="n")
        self.game_chip = tk.Label(
            chips, text="", bg=PALETTE["soft_blue"], fg="#174EA6",
            font=("Segoe UI", 9, "bold"), padx=11, pady=7
        )
        self.game_chip.pack(side="left")
        tk.Label(
            chips, text="LOCAL DATA", bg="#E8F7EE", fg="#166534",
            font=("Segoe UI", 9, "bold"), padx=11, pady=7
        ).pack(side="left", padx=(7, 0))

        intro = tk.Frame(
            parent, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        intro.pack(fill="x", pady=(0, 12))
        self.intro_accent = tk.Frame(intro, width=5, bg=PALETTE["blue_bright"])
        self.intro_accent.pack(side="left", fill="y")
        intro_body = tk.Frame(intro, bg=PALETTE["surface"])
        intro_body.pack(side="left", fill="x", expand=True, padx=14, pady=11)
        tk.Label(
            intro_body, text="Selection dashboard", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")
        tk.Label(
            intro_body,
            text="Historical data informs ranking; coverage mathematics controls how your ticket budget is distributed.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 10),
            wraplength=900, justify="left"
        ).pack(anchor="w", pady=(3, 0))

        # Modern navigation replacing the classic Tk notebook tabs.
        self.nav_bar = tk.Frame(parent, bg=PALETTE["bg"])
        self.nav_bar.pack(fill="x", pady=(0, 8))
        self.nav_buttons = {}
        nav_items = [
            ("tickets", "Tickets"),
            ("analysis", "Number Analysis"),
            ("data_manager", "Data Manager"),
            ("updater", "Results Updater"),
            ("backtest", "Backtest"),
            ("strategy_lab", "Strategy Lab"),
            ("method", "Methodology"),
        ]
        for key, label in nav_items:
            button = tk.Button(
                self.nav_bar, text=label, command=lambda k=key: self._navigate(k),
                bg="#E8EEF6", fg=PALETTE["soft_ink"], activebackground="#DDEBFF",
                activeforeground=PALETTE["ink"], relief="flat", bd=0, cursor="hand2",
                takefocus=True, highlightthickness=2, highlightbackground=PALETTE["bg"],
                highlightcolor=PALETTE["blue_bright"], font=("Segoe UI", 10, "bold"),
                padx=18, pady=10
            )
            button.pack(side="left", padx=(0, 7))
            self.nav_buttons[key] = button

        self.view_stack = tk.Frame(parent, bg=PALETTE["bg"])
        self.view_stack.pack(fill="both", expand=True)
        self.view_stack.grid_rowconfigure(0, weight=1)
        self.view_stack.grid_columnconfigure(0, weight=1)

        self.views = {}
        self._build_tickets_view()
        self._build_analysis_view()
        self._build_data_manager_view()
        self._build_updater_view()
        self._build_backtest_view()
        self._build_strategy_lab_view()
        self._build_method_view()
        self._render_methodology()
        self._show_view("tickets")

        footer = tk.Frame(parent, bg=PALETTE["bg"])
        footer.pack(fill="x", pady=(8, 0))
        tk.Label(
            footer, text="Alt+G Generate  •  Alt+A Analyse  •  Alt+B Backtest  •  Alt+L Strategy Lab  •  Ctrl+D Data Manager  •  Ctrl+Shift+U Results Updater  •  Ctrl+S Save",
            bg=PALETTE["bg"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(side="left")
        tk.Label(
            footer, textvariable=self.footer_var, bg=PALETTE["bg"], fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9, "bold")
        ).pack(side="right")

        self._update_strategy_help()
        self._update_control_states()

    def _navigate(self, key: str):
        """Open a workspace section and perform the action users expect from that section."""
        if key == "analysis":
            self.analyse()
            return
        if key == "data_manager":
            self.refresh_data_audit()
            self._show_view("data_manager")
            return
        if key == "updater":
            self._refresh_updater_local_rows()
        if key == "method":
            self._render_methodology()
        self._show_view(key)

    def _show_view(self, key: str):
        view = self.views[key]
        view.tkraise()
        for name, button in self.nav_buttons.items():
            if name == key:
                button.configure(bg="#DCEBFF", fg="#174EA6", highlightbackground=PALETTE["blue_bright"])
            else:
                button.configure(bg="#E8EEF6", fg=PALETTE["soft_ink"], highlightbackground=PALETTE["bg"])

    def _build_analysis_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["analysis"] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 10))
        title_box = tk.Frame(header, bg=PALETTE["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box, text="Number analysis", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Historical frequency, recent activity and gaps are descriptive signals — not guarantees about the next draw.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))
        analysis_controls = tk.Frame(header, bg=PALETTE["surface"])
        analysis_controls.pack(side="right")
        tk.Label(
            analysis_controls, text="History window", bg=PALETTE["surface"], fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 7))
        self.analysis_history_combo = ttk.Combobox(
            analysis_controls, textvariable=self.analysis_history_window_var,
            values=("All", "250", "100", "50", "20"), state="readonly", width=7,
            style="DrawWise.TCombobox", font=("Segoe UI", 9), takefocus=True
        )
        self.analysis_history_combo.pack(side="left", padx=(0, 8))
        self.analysis_history_combo.bind("<<ComboboxSelected>>", lambda _e: self.analyse())
        self._workspace_button(analysis_controls, "Refresh analysis", self.analyse, subtle=True).pack(side="left")

        self.analysis_summary = tk.Frame(frame, bg=PALETTE["surface"])
        self.analysis_summary.pack(fill="x", padx=20, pady=(0, 10))
        self.analysis_summary_vars = {
            "draws": tk.StringVar(value="—"),
            "recent": tk.StringVar(value="—"),
            "latest": tk.StringVar(value="—"),
            "range": tk.StringVar(value="—"),
        }
        for i, (label, key) in enumerate([
            ("Draws analysed", "draws"),
            ("Recent window", "recent"),
            ("Latest draw", "latest"),
            ("Number range", "range"),
        ]):
            card = tk.Frame(self.analysis_summary, bg="#F8FAFC", highlightthickness=1, highlightbackground="#D9E3EE")
            card.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 5, 0))
            tk.Label(card, textvariable=self.analysis_summary_vars[key], bg="#F8FAFC", fg=PALETTE["ink"],
                     font=("Segoe UI", 13, "bold"), pady=4).pack()
            tk.Label(card, text=label, bg="#F8FAFC", fg=PALETTE["soft_ink"],
                     font=("Segoe UI", 8, "bold"), pady=3).pack()

        # Game-aware analysis selector. Multi-ball games keep main and special-ball
        # statistics separate instead of mixing unlike number universes.
        self.analysis_domain_var = tk.StringVar(value="main")
        self.analysis_domain_bar = tk.Frame(frame, bg=PALETTE["surface"])
        self.analysis_domain_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.analysis_main_button = self._workspace_button(
            self.analysis_domain_bar, "Main numbers", lambda: self._render_analysis_domain("main"), subtle=True
        )
        self.analysis_main_button.pack(side="left", padx=(0, 7))
        self.analysis_special_button = self._workspace_button(
            self.analysis_domain_bar, "Special balls", lambda: self._render_analysis_domain("special"), subtle=True
        )
        self.analysis_special_button.pack(side="left")

        bands = tk.Frame(frame, bg=PALETTE["surface"])
        bands.pack(fill="x", padx=20, pady=(0, 10))
        self.analysis_band_vars = {
            "hot": tk.StringVar(value="Run analysis to see higher-frequency numbers."),
            "recent": tk.StringVar(value="Run analysis to see recent movers."),
            "gaps": tk.StringVar(value="Run analysis to see longest gaps."),
        }
        band_specs = [
            ("Higher frequency", "hot", "#FFF4E5", "#8A4B08"),
            ("Recent movers", "recent", "#EAF3FF", "#174EA6"),
            ("Longest gaps", "gaps", "#F3E8FF", "#6B21A8"),
        ]
        for i, (label, key, bg, fg) in enumerate(band_specs):
            card = tk.Frame(bands, bg=bg, highlightthickness=1, highlightbackground="#D9E3EE")
            card.pack(side="left", fill="both", expand=True, padx=(0 if i == 0 else 5, 0))
            tk.Label(card, text=label, bg=bg, fg=fg, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
            tk.Label(card, textvariable=self.analysis_band_vars[key], bg=bg, fg=PALETTE["ink"],
                     font=("Segoe UI", 10, "bold"), wraplength=290, justify="left").pack(anchor="w", padx=10, pady=(0, 9))

        table_wrap = tk.Frame(frame, bg=PALETTE["surface"])
        table_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        columns = ("number", "count", "recent", "gap", "freq", "recentidx", "score", "status")
        self.analysis_tree = ttk.Treeview(
            table_wrap, columns=columns, show="headings", style="Analysis.Treeview", takefocus=True
        )
        headings = {
            "number": "Number", "count": "All draws", "recent": "Recent", "gap": "Draws since seen",
            "freq": "Frequency index", "recentidx": "Recent index", "score": "Combined score", "status": "Status"
        }
        widths = {"number": 75, "count": 90, "recent": 85, "gap": 115, "freq": 110, "recentidx": 105, "score": 110, "status": 120}
        for col in columns:
            self.analysis_tree.heading(col, text=headings[col])
            self.analysis_tree.column(col, width=widths[col], anchor="center", stretch=True)
        self.analysis_tree.tag_configure("hot", background="#FFF7E6")
        self.analysis_tree.tag_configure("cold", background="#EFF6FF")
        self.analysis_tree.tag_configure("balanced", background="#FFFFFF")
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.analysis_tree.yview, style="DrawWise.Vertical.TScrollbar")
        self.analysis_tree.configure(yscrollcommand=scroll.set)
        self.analysis_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.analysis_special_var = tk.StringVar(value="")
        tk.Label(
            frame, textvariable=self.analysis_special_var, bg="#F8FAFC", fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=950,
            highlightthickness=1, highlightbackground="#D9E3EE", padx=10, pady=8
        ).pack(fill="x", padx=20, pady=(0, 12))

    def _build_data_manager_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["data_manager"] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 9))
        title_box = tk.Frame(header, bg=PALETTE["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box, text="Historical Data Manager", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Audit, merge, clean and back up the draw history that feeds analysis and backtesting.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))
        controls = tk.Frame(header, bg=PALETTE["surface"])
        controls.pack(side="right")
        self._workspace_button(controls, "Refresh audit", self.refresh_data_audit, subtle=True).pack(side="left", padx=(0, 7))
        self._workspace_button(controls, "Import file", self.update_history).pack(side="left", padx=(0, 7))
        self._workspace_button(controls, "Import folder", self.import_history_folder, subtle=True).pack(side="left", padx=(0, 7))
        self._workspace_button(controls, "Export clean CSV", self.export_clean_history, subtle=True).pack(side="left")

        summary = tk.Frame(frame, bg=PALETTE["surface"])
        summary.pack(fill="x", padx=20, pady=(0, 10))
        self.data_manager_summary_vars = {
            "quality": tk.StringVar(value="—"),
            "valid": tk.StringVar(value="—"),
            "depth": tk.StringVar(value="—"),
            "completeness": tk.StringVar(value="—"),
            "duplicates": tk.StringVar(value="—"),
            "gaps": tk.StringVar(value="—"),
        }
        for i, (label, key) in enumerate([
            ("Quality score", "quality"),
            ("Analysis-ready / stored", "valid"),
            ("Depth target", "depth"),
            ("Cadence complete", "completeness"),
            ("Duplicates", "duplicates"),
            ("Suspected gaps", "gaps"),
        ]):
            card = tk.Frame(summary, bg="#F8FAFC", highlightthickness=1, highlightbackground="#D9E3EE")
            card.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 5, 0))
            tk.Label(
                card, textvariable=self.data_manager_summary_vars[key], bg="#F8FAFC", fg=PALETTE["ink"],
                font=("Segoe UI", 13, "bold"), pady=4
            ).pack()
            tk.Label(
                card, text=label, bg="#F8FAFC", fg=PALETTE["soft_ink"],
                font=("Segoe UI", 8, "bold"), pady=3
            ).pack()

        self.data_manager_status_var = tk.StringVar(value="Open Data Manager to audit the selected game's local history file.")
        self.data_manager_status = tk.Label(
            frame, textvariable=self.data_manager_status_var, bg="#EEF5FF", fg="#17365D",
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=1100,
            highlightthickness=1, highlightbackground="#C7DCF7", padx=12, pady=9
        )
        self.data_manager_status.pack(fill="x", padx=20, pady=(0, 9))

        profile = tk.Frame(frame, bg="#F8FAFC", highlightthickness=1, highlightbackground="#D9E3EE")
        profile.pack(fill="x", padx=20, pady=(0, 9))
        self.data_profile_var = tk.StringVar(value="Data profile will appear after the audit.")
        tk.Label(
            profile, textvariable=self.data_profile_var, bg="#F8FAFC", fg=PALETTE["ink"],
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=1080, padx=12, pady=9
        ).pack(fill="x")
        self.data_windows_var = tk.StringVar(value="Analysis windows: 20 • 50 • 100 • 250 • All")
        tk.Label(
            profile, textvariable=self.data_windows_var, bg="#F8FAFC", fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9, "bold"), anchor="w", padx=12, pady=0
        ).pack(fill="x", pady=(0, 9))
        self.data_stability_var = tk.StringVar(value="Ranking stability will appear after the audit.")
        tk.Label(
            profile, textvariable=self.data_stability_var, bg="#F8FAFC", fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=1080, padx=12, pady=0
        ).pack(fill="x", pady=(0, 9))
        self.data_era_var = tk.StringVar(value="Rule-era compatibility will appear after the audit.")
        tk.Label(
            profile, textvariable=self.data_era_var, bg="#EEF5FF", fg="#17365D",
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=1080, padx=12, pady=7,
            highlightthickness=1, highlightbackground="#C7DCF7"
        ).pack(fill="x", padx=10, pady=(0, 9))

        table_head = tk.Frame(frame, bg=PALETTE["surface"])
        table_head.pack(fill="x", padx=20, pady=(0, 6))
        tk.Label(
            table_head, text="Data quality findings", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 10, "bold")
        ).pack(side="left")
        table_actions = tk.Frame(table_head, bg=PALETTE["surface"])
        table_actions.pack(side="right")
        self._workspace_button(table_actions, "Rule-era guide", self.show_rule_era_guide, subtle=True).pack(side="left", padx=(0, 7))
        self._workspace_button(table_actions, "Open data folder", self.open_data_folder, subtle=True).pack(side="left", padx=(0, 7))
        self._workspace_button(table_actions, "Open backups", self.open_backups_folder, subtle=True).pack(side="left")

        table_wrap = tk.Frame(frame, bg=PALETTE["surface"])
        table_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        columns = ("severity", "category", "where", "detail")
        self.data_manager_tree = ttk.Treeview(
            table_wrap, columns=columns, show="headings", style="Analysis.Treeview", takefocus=True, height=9
        )
        headings = {"severity": "Status", "category": "Finding", "where": "Date / row", "detail": "Detail"}
        widths = {"severity": 78, "category": 120, "where": 115, "detail": 600}
        for col in columns:
            self.data_manager_tree.heading(col, text=headings[col])
            self.data_manager_tree.column(col, width=widths[col], anchor="w", stretch=(col == "detail"))
        self.data_manager_tree.tag_configure("ok", background="#E8F7EE")
        self.data_manager_tree.tag_configure("review", background="#FFF8E6")
        self.data_manager_tree.tag_configure("warning", background="#FFF4E5")
        self.data_manager_tree.tag_configure("error", background="#FFF1F2")
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.data_manager_tree.yview, style="DrawWise.Vertical.TScrollbar")
        self.data_manager_tree.configure(yscrollcommand=scroll.set)
        self.data_manager_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        note = tk.Label(
            frame,
            text=("Suspected gaps are inferred from the weekdays present in your own file; they are prompts to verify against an official result source, "
                  "not proof that a draw is missing. V4.1 also classifies archive rows by rule era, keeps incompatible legacy matrices out of current strategy analysis, and previews every merge before local history is changed."),
            bg="#F8FAFC", fg=PALETTE["soft_ink"], font=("Segoe UI", 9),
            anchor="w", justify="left", wraplength=1080, highlightthickness=1,
            highlightbackground="#D9E3EE", padx=10, pady=8
        )
        note.pack(fill="x", padx=20, pady=(0, 12))

    def _build_updater_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["updater"] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 9))
        title_box = tk.Frame(header, bg=PALETTE["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box, text="Verified Results Updater", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Check configured official result sources, preview differences, then approve any local history change.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))

        controls = tk.Frame(frame, bg=PALETTE["surface"])
        controls.pack(fill="x", padx=20, pady=(0, 9))
        self.updater_check_selected_btn = self._workspace_button(
            controls, "Check selected", self.check_selected_game_online
        )
        self.updater_check_selected_btn.pack(side="left", padx=(0, 7))
        self.updater_check_all_btn = self._workspace_button(
            controls, "Check all sources", self.check_all_games_online, subtle=True
        )
        self.updater_check_all_btn.pack(side="left", padx=(0, 7))
        self.updater_import_btn = self._workspace_button(
            controls, "Import checked update", self.import_selected_online_update, subtle=True
        )
        self.updater_import_btn.pack(side="left", padx=(0, 7))
        self.updater_open_source_btn = self._workspace_button(
            controls, "Open official page", self.open_selected_official_source, subtle=True
        )
        self.updater_open_source_btn.pack(side="left", padx=(0, 7))
        self.updater_save_diagnostic_btn = self._workspace_button(
            controls, "Save diagnostic", self.save_selected_source_diagnostic, subtle=True
        )
        self.updater_save_diagnostic_btn.configure(state="disabled")
        self.updater_save_diagnostic_btn.pack(side="left")

        summary = tk.Frame(frame, bg=PALETTE["surface"])
        summary.pack(fill="x", padx=20, pady=(0, 10))
        self.updater_summary_vars = {
            "sources": tk.StringVar(value=f"{source_feed_count()} / {len(ALL_GAMES)}"),
            "checked": tk.StringVar(value="0"),
            "updates": tk.StringVar(value="0"),
            "errors": tk.StringVar(value="0"),
        }
        for i, (label, key) in enumerate([
            ("Sources / games", "sources"),
            ("Sources checked", "checked"),
            ("Game updates", "updates"),
            ("Source errors", "errors"),
        ]):
            card = tk.Frame(summary, bg="#F8FAFC", highlightthickness=1, highlightbackground="#D9E3EE")
            card.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 5, 0))
            tk.Label(
                card, textvariable=self.updater_summary_vars[key], bg="#F8FAFC", fg=PALETTE["ink"],
                font=("Segoe UI", 13, "bold"), pady=4
            ).pack()
            tk.Label(
                card, text=label, bg="#F8FAFC", fg=PALETTE["soft_ink"],
                font=("Segoe UI", 8, "bold"), pady=3
            ).pack()

        self.updater_status_var = tk.StringVar(
            value="No online check has run yet. Local history is never changed by a check."
        )
        self.updater_status = tk.Label(
            frame, textvariable=self.updater_status_var, bg="#EEF5FF", fg="#17365D",
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=800,
            highlightthickness=1, highlightbackground="#C7DCF7", padx=12, pady=9
        )
        self.updater_status.pack(fill="x", padx=20, pady=(0, 9))

        safety = tk.Frame(frame, bg="#E8F7EE", highlightthickness=1, highlightbackground="#B8E1C8")
        safety.pack(fill="x", padx=20, pady=(0, 9))
        tk.Label(
            safety,
            text=("Safety model: CHECK → VALIDATE → PREVIEW → YOU APPROVE → BACKUP → MERGE. "
                  "DrawWise does not silently overwrite local draw history and does not bypass website access controls."),
            bg="#E8F7EE", fg="#166534", font=("Segoe UI", 9, "bold"), anchor="w",
            justify="left", wraplength=800, padx=12, pady=9
        ).pack(fill="x")

        table_wrap = tk.Frame(frame, bg=PALETTE["surface"])
        table_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        columns = ("game", "provider", "local", "online", "new", "corrected", "status")
        self.updater_tree = ttk.Treeview(
            table_wrap, columns=columns, show="headings", style="Analysis.Treeview", takefocus=True, height=12
        )
        headings = {
            "game": "Game", "provider": "Source", "local": "Local latest",
            "online": "Official latest", "new": "New", "corrected": "Corrected",
            "status": "Status",
        }
        widths = {
            "game": 155, "provider": 165, "local": 105, "online": 105,
            "new": 50, "corrected": 78, "status": 245,
        }
        for col in columns:
            self.updater_tree.heading(col, text=headings[col])
            self.updater_tree.column(col, width=widths[col], anchor="w", stretch=(col == "status"))
        self.updater_tree.tag_configure("ok", background="#E8F7EE")
        self.updater_tree.tag_configure("update", background="#FFF8E6")
        self.updater_tree.tag_configure("error", background="#FFF1F2")
        self.updater_tree.tag_configure("unchecked", background="#FFFFFF")
        scroll = ttk.Scrollbar(
            table_wrap, orient="vertical", command=self.updater_tree.yview,
            style="DrawWise.Vertical.TScrollbar"
        )
        self.updater_tree.configure(yscrollcommand=scroll.set)
        self.updater_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.updater_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_updater_action_states())

        self.updater_detail_var = tk.StringVar(
            value="Select a row after checking to see the source and merge preview."
        )
        tk.Label(
            frame, textvariable=self.updater_detail_var, bg="#F8FAFC", fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=800,
            highlightthickness=1, highlightbackground="#D9E3EE", padx=10, pady=8
        ).pack(fill="x", padx=20, pady=(0, 12))
        self._refresh_updater_local_rows()

    def _build_backtest_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["backtest"] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 10))
        title_box = tk.Frame(header, bg=PALETTE["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(title_box, text="Walk-forward backtest", bg=PALETTE["surface"], fg=PALETTE["ink"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(
            title_box,
            text="Each historical target draw is hidden until after the strategy has selected its portfolio.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))
        backtest_controls = tk.Frame(header, bg=PALETTE["surface"])
        backtest_controls.pack(side="right")
        tk.Label(
            backtest_controls, text="Test horizon", bg=PALETTE["surface"], fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 7))
        self.backtest_horizon_var = tk.StringVar(value="All")
        self.backtest_horizon_combo = ttk.Combobox(
            backtest_controls, textvariable=self.backtest_horizon_var,
            values=("All", "100", "50", "25"), state="readonly", width=6,
            style="DrawWise.TCombobox", font=("Segoe UI", 9), takefocus=True
        )
        self.backtest_horizon_combo.pack(side="left", padx=(0, 8))
        self.backtest_run_button = self._workspace_button(backtest_controls, "Run backtest", self.backtest)
        self.backtest_run_button.pack(side="left")

        self.backtest_summary = tk.Frame(frame, bg=PALETTE["surface"])
        self.backtest_summary.pack(fill="x", padx=20, pady=(0, 10))
        self.backtest_summary_vars = {
            "tested": tk.StringVar(value="—"),
            "strategy": tk.StringVar(value="—"),
            "random": tk.StringVar(value="—"),
            "lift": tk.StringVar(value="—"),
            "best": tk.StringVar(value="—"),
        }
        for i, (label, key) in enumerate([
            ("Out-of-sample draws", "tested"),
            ("Strategy avg. best match", "strategy"),
            ("Random benchmark", "random"),
            ("Difference", "lift"),
            ("Best historical match", "best"),
        ]):
            card = tk.Frame(self.backtest_summary, bg="#F8FAFC", highlightthickness=1, highlightbackground="#D9E3EE")
            card.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 5, 0))
            tk.Label(card, textvariable=self.backtest_summary_vars[key], bg="#F8FAFC", fg=PALETTE["ink"],
                     font=("Segoe UI", 13, "bold"), pady=4).pack()
            tk.Label(card, text=label, bg="#F8FAFC", fg=PALETTE["soft_ink"],
                     font=("Segoe UI", 8, "bold"), pady=3).pack()

        self.backtest_status_var = tk.StringVar(value="Backtest not run yet. Select your game and strategy, then choose Run backtest.")
        self.backtest_status = tk.Label(
            frame, textvariable=self.backtest_status_var, bg="#EEF5FF", fg="#17365D",
            font=("Segoe UI", 10, "bold"), anchor="w", justify="left", wraplength=1000,
            highlightthickness=1, highlightbackground="#C7DCF7", padx=12, pady=10
        )
        self.backtest_status.pack(fill="x", padx=20, pady=(0, 8))

        progress_wrap = tk.Frame(frame, bg=PALETTE["surface"])
        progress_wrap.pack(fill="x", padx=20, pady=(0, 10))
        self.backtest_progress_var = tk.DoubleVar(value=0.0)
        self.backtest_progress = ttk.Progressbar(
            progress_wrap, variable=self.backtest_progress_var, maximum=100, mode="determinate",
            style="Backtest.Horizontal.TProgressbar"
        )
        self.backtest_progress.pack(side="left", fill="x", expand=True)
        self.backtest_progress_text_var = tk.StringVar(value="Ready")
        tk.Label(
            progress_wrap, textvariable=self.backtest_progress_text_var, bg=PALETTE["surface"],
            fg=PALETTE["soft_ink"], font=("Segoe UI", 9, "bold"), padx=10
        ).pack(side="left")
        self.backtest_cancel_button = self._workspace_button(progress_wrap, "Cancel", self.cancel_backtest, subtle=True)
        self.backtest_cancel_button.configure(state="disabled")
        self.backtest_cancel_button.pack(side="right")

        self.backtest_threshold_var = tk.StringVar(value="Match thresholds will appear here after the test.")
        tk.Label(
            frame, textvariable=self.backtest_threshold_var, bg="#F8FAFC", fg=PALETTE["ink"],
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=1000,
            highlightthickness=1, highlightbackground="#D9E3EE", padx=12, pady=9
        ).pack(fill="x", padx=20, pady=(0, 10))

        body = tk.Frame(frame, bg=PALETTE["surface"])
        body.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.backtest_text = tk.Text(
            body, wrap="word", font=("Segoe UI", 10), padx=14, pady=12,
            bg="#FBFDFF", fg=PALETTE["ink"], relief="flat", state="disabled",
            highlightthickness=1, highlightbackground="#E2E8F0", spacing1=2, spacing3=3
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.backtest_text.yview, style="DrawWise.Vertical.TScrollbar")
        self.backtest_text.configure(yscrollcommand=scroll.set)
        self.backtest_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_strategy_lab_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["strategy_lab"] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 9))
        title_box = tk.Frame(header, bg=PALETTE["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box, text="Strategy Lab", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Compare six selection methods on the same hidden historical draws, ticket budget and shared random benchmark.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))

        controls = tk.Frame(header, bg=PALETTE["surface"])
        controls.pack(side="right")
        tk.Label(
            controls, text="Horizon", bg=PALETTE["surface"], fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 7))
        self.strategy_lab_horizon_var = tk.StringVar(value="All")
        self.strategy_lab_horizon_combo = ttk.Combobox(
            controls, textvariable=self.strategy_lab_horizon_var,
            values=("All", "100", "50", "25"), state="readonly", width=6,
            style="DrawWise.TCombobox", font=("Segoe UI", 9), takefocus=True
        )
        self.strategy_lab_horizon_combo.pack(side="left", padx=(0, 10))
        tk.Label(
            controls, text="Random trials / draw", bg=PALETTE["surface"], fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 7))
        self.strategy_lab_trials_var = tk.IntVar(value=50)
        self.strategy_lab_trials_spin = ttk.Spinbox(
            controls, from_=10, to=200, increment=10, width=6,
            textvariable=self.strategy_lab_trials_var, style="DrawWise.TSpinbox",
            font=("Segoe UI", 9), takefocus=True
        )
        self.strategy_lab_trials_spin.pack(side="left", padx=(0, 8))
        self.strategy_lab_run_button = self._workspace_button(
            controls, "Run comparison", self.run_strategy_lab
        )
        self.strategy_lab_run_button.pack(side="left")

        summary = tk.Frame(frame, bg=PALETTE["surface"])
        summary.pack(fill="x", padx=20, pady=(0, 9))
        self.strategy_lab_summary_vars = {
            "tested": tk.StringVar(value="—"),
            "count": tk.StringVar(value=str(len(STRATEGY_LAB_STRATEGIES))),
            "benchmark": tk.StringVar(value="—"),
            "winner": tk.StringVar(value="—"),
            "lift": tk.StringVar(value="—"),
        }
        for i, (label, key) in enumerate([
            ("Tests / strategy", "tested"),
            ("Strategies compared", "count"),
            ("Shared main benchmark", "benchmark"),
            ("Leader difference", "lift"),
        ]):
            card = tk.Frame(summary, bg="#F8FAFC", highlightthickness=1, highlightbackground="#D9E3EE")
            card.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 5, 0))
            size = 13
            tk.Label(
                card, textvariable=self.strategy_lab_summary_vars[key], bg="#F8FAFC", fg=PALETTE["ink"],
                font=("Segoe UI", size, "bold"), pady=4, wraplength=180
            ).pack()
            tk.Label(
                card, text=label, bg="#F8FAFC", fg=PALETTE["soft_ink"],
                font=("Segoe UI", 8, "bold"), pady=3
            ).pack()

        self.strategy_lab_status_var = tk.StringVar(
            value="Ready. All strategies will use the same historical targets, line budget and random control."
        )
        self.strategy_lab_status = tk.Label(
            frame, textvariable=self.strategy_lab_status_var, bg="#EEF5FF", fg="#17365D",
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=1050,
            highlightthickness=1, highlightbackground="#C7DCF7", padx=12, pady=9
        )
        self.strategy_lab_status.pack(fill="x", padx=20, pady=(0, 8))

        progress_wrap = tk.Frame(frame, bg=PALETTE["surface"])
        progress_wrap.pack(fill="x", padx=20, pady=(0, 9))
        self.strategy_lab_progress_var = tk.DoubleVar(value=0.0)
        self.strategy_lab_progress = ttk.Progressbar(
            progress_wrap, variable=self.strategy_lab_progress_var, maximum=100, mode="determinate",
            style="Backtest.Horizontal.TProgressbar"
        )
        self.strategy_lab_progress.pack(side="left", fill="x", expand=True)
        self.strategy_lab_progress_text_var = tk.StringVar(value="Ready")
        tk.Label(
            progress_wrap, textvariable=self.strategy_lab_progress_text_var, bg=PALETTE["surface"],
            fg=PALETTE["soft_ink"], font=("Segoe UI", 9, "bold"), padx=10
        ).pack(side="left")
        self.strategy_lab_cancel_button = self._workspace_button(
            progress_wrap, "Cancel", self.cancel_strategy_lab, subtle=True
        )
        self.strategy_lab_cancel_button.configure(state="disabled")
        self.strategy_lab_cancel_button.pack(side="right")

        result_head = tk.Frame(frame, bg=PALETTE["surface"])
        result_head.pack(fill="x", padx=20, pady=(0, 6))
        tk.Label(
            result_head, text="Side-by-side historical results", bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 10, "bold")
        ).pack(side="left")
        self.strategy_lab_use_button = self._workspace_button(
            result_head, "Use top strategy", self.use_strategy_lab_winner, subtle=True
        )
        self.strategy_lab_use_button.configure(state="disabled")
        self.strategy_lab_use_button.pack(side="right")

        table_wrap = tk.Frame(frame, bg=PALETTE["surface"])
        table_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        columns = ("rank", "strategy", "avg", "lift", "complete", "special", "beat", "three", "four", "best", "status")
        self.strategy_lab_tree = ttk.Treeview(
            table_wrap, columns=columns, show="headings", style="Analysis.Treeview", takefocus=True, height=7
        )
        headings = {
            "rank": "Rank", "strategy": "Strategy", "avg": "Main avg",
            "lift": "Main Δ", "complete": "Complete Δ", "special": "Special cov",
            "beat": "Beat random", "three": "3+", "four": "4+",
            "best": "Best pattern", "status": "Status"
        }
        widths = {
            "rank": 42, "strategy": 160, "avg": 68, "lift": 65,
            "complete": 78, "special": 72, "beat": 76, "three": 45,
            "four": 45, "best": 75, "status": 90
        }
        for col in columns:
            self.strategy_lab_tree.heading(col, text=headings[col])
            self.strategy_lab_tree.column(
                col, width=widths[col], anchor="w" if col == "strategy" else "center",
                stretch=(col == "strategy")
            )
        self.strategy_lab_tree.tag_configure("winner", background="#E8F7EE")
        self.strategy_lab_tree.tag_configure("positive", background="#F1F9F4")
        self.strategy_lab_tree.tag_configure("neutral", background="#FFF8E6")
        self.strategy_lab_tree.tag_configure("negative", background="#FFF1F2")
        self.strategy_lab_tree.tag_configure("error", background="#F8FAFC")
        scroll = ttk.Scrollbar(
            table_wrap, orient="vertical", command=self.strategy_lab_tree.yview,
            style="DrawWise.Vertical.TScrollbar"
        )
        self.strategy_lab_tree.configure(yscrollcommand=scroll.set)
        self.strategy_lab_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.strategy_lab_notes_var = tk.StringVar(
            value="Strategy Lab compares main-number performance and, where applicable, separately selected special-ball performance."
        )
        tk.Label(
            frame, textvariable=self.strategy_lab_notes_var, bg="#F8FAFC", fg=PALETTE["soft_ink"],
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=1050,
            highlightthickness=1, highlightbackground="#D9E3EE", padx=10, pady=8
        ).pack(fill="x", padx=20, pady=(0, 12))

    def _build_method_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["method"] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 10))
        tk.Label(header, text="Methodology", bg=PALETTE["surface"], fg=PALETTE["ink"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(
            header, text="What the selected strategy does, what changes coverage, and what historical data cannot prove.",
            bg=PALETTE["surface"], fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(frame, bg="#F7F9FC")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        canvas = tk.Canvas(body, bg="#F7F9FC", highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview, style="DrawWise.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.method_cards = tk.Frame(canvas, bg="#F7F9FC")
        self.method_window = canvas.create_window((0, 0), window=self.method_cards, anchor="nw")
        self.method_cards.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self.method_window, width=e.width))
        self.method_canvas = canvas

    def _method_card(self, title: str, body: str, accent: str = "#2563EB"):
        card = tk.Frame(self.method_cards, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D9E3EE")
        card.pack(fill="x", padx=8, pady=6)
        tk.Frame(card, bg=accent, width=5).pack(side="left", fill="y")
        text_box = tk.Frame(card, bg="#FFFFFF")
        text_box.pack(side="left", fill="both", expand=True, padx=13, pady=11)
        tk.Label(text_box, text=title, bg="#FFFFFF", fg=PALETTE["ink"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(text_box, text=body, bg="#FFFFFF", fg=PALETTE["soft_ink"], font=("Segoe UI", 9),
                 justify="left", wraplength=940).pack(anchor="w", pady=(4, 0))

    def _render_methodology(self):
        if not hasattr(self, "method_cards"):
            return
        for child in self.method_cards.winfo_children():
            child.destroy()
        strategy = self.strategy_var.get()
        cfg = BY_NAME.get(self.game_var.get(), ALL_GAMES[0])
        self._method_card(
            f"Selected strategy — {strategy}",
            STRATEGY_HELP.get(strategy, "The selected strategy structures a set of distinct lottery lines."),
            GAME_ACCENTS.get(cfg.key, "#2563EB"),
        )
        if strategy in {"Condensed Portfolio", "Abbreviated Wheel", "Full Wheel", "Key Number Wheel", "Historical Ranked", "Hot + Cold Blend"}:
            coverage = (
                "This strategy concentrates lines inside a selected base pool. Pair/triple coverage and overlap determine how efficiently "
                "your fixed number of lines covers combinations inside that pool. A full wheel owns every valid combination inside the pool; "
                "an abbreviated or condensed wheel owns only a structured subset."
            )
        else:
            coverage = (
                "This strategy spreads lines across the wider number universe. The genuine mathematical benefit comes from keeping tickets distinct "
                "and reducing repeated overlap so your fixed ticket budget covers more different combinations."
            )
        self._method_card("Coverage mathematics", coverage, "#0E7490")
        self._method_card(
            "Historical inputs",
            "DrawWise measures long-run frequency, recent-window frequency and draws-since-seen. These statistics are used as a consistent ranking overlay. "
            "They describe the sample; they do not alter the physical probability of an independent ball on the next draw.",
            "#D97706",
        )
        profile = rule_profile(cfg)
        era_text = (
            f"Current analysis universe: {profile.current_analysis_label}. "
            "Archive rows from incompatible historical number matrices may be retained for audit/research, but DrawWise excludes them from current strategy analysis and backtesting."
        )
        if profile.current_analysis_start:
            era_text += f" Current analysis starts at {profile.current_analysis_start:%d-%b-%Y}."
        if profile.purchase_note:
            era_text += " " + profile.purchase_note
        self._method_card("Rule-era protection", era_text, "#2563EB")
        if cfg.special_pick:
            self._method_card(
                f"Game-aware {cfg.special_name} analytics",
                f"{cfg.name} has a separate {cfg.special_name} number universe ({cfg.special_min}–{cfg.special_max}). DrawWise analyses it separately from the main balls, "
                "measures special-ball coverage across generated lines, and includes main+special hit patterns in backtests. The complete-ticket component score is descriptive, not a prize-value scale.",
                "#F59E0B",
            )
        self._method_card(
            "Walk-forward testing",
            "For each test date, DrawWise trains only on draws that occurred before that date, generates a portfolio, then compares the hidden result with a random baseline. "
            "A positive result is evidence about that historical test window only; it is not proof of future predictive power.",
            "#7C3AED",
        )
        self._method_card(
            "Crowd-pattern controls",
            "Birthday-heavy lines, obvious sequences and regular visual patterns are not less likely to be drawn. Avoiding them is only a possible prize-sharing tactic: "
            "if a rare jackpot occurs, a less commonly chosen line may reduce the chance of splitting it with other players.",
            "#15803D",
        )
        self._method_card(
            "What actually improves jackpot probability",
            "Within the same game, owning more DISTINCT complete ticket combinations increases jackpot probability, while spending rises with the number of purchased lines. "
            "Where a current purchase receives more than one physical round, DrawWise compounds the line/portfolio probability across those rounds. Wheeling improves organisation and conditional coverage; it does not make a single valid line intrinsically luckier.",
            "#DC2626",
        )
        self.method_canvas.yview_moveto(0)

    def _make_text_view(self, key: str, title: str) -> tk.Text:
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views[key] = frame

        header = tk.Frame(frame, bg=PALETTE["surface"])
        header.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(
            header, text=title, bg=PALETTE["surface"], fg=PALETTE["ink"],
            font=("Segoe UI", 14, "bold")
        ).pack(side="left")

        body = tk.Frame(frame, bg=PALETTE["surface"])
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        text = tk.Text(
            body, wrap="word", font=("Consolas", 11), padx=15, pady=14,
            bg="#FBFDFF", fg=PALETTE["ink"], insertbackground=PALETTE["ink"],
            relief="flat", highlightthickness=1, highlightbackground="#E2E8F0",
            selectbackground="#BFDBFE", selectforeground=PALETTE["ink"],
            spacing1=2, spacing3=2, takefocus=True
        )
        scroll = ttk.Scrollbar(
            body, orient="vertical", command=text.yview, style="DrawWise.Vertical.TScrollbar"
        )
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return text

    def _build_tickets_view(self):
        frame = tk.Frame(
            self.view_stack, bg=PALETTE["surface"], highlightthickness=1,
            highlightbackground=PALETTE["line"]
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.views["tickets"] = frame

        toolbar = tk.Frame(frame, bg=PALETTE["surface"])
        toolbar.pack(fill="x", padx=20, pady=(16, 10))
        title_box = tk.Frame(toolbar, bg=PALETTE["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        self.ticket_heading_var = tk.StringVar(value="Your tickets")
        self.ticket_subheading_var = tk.StringVar(value="Choose your game and strategy, then generate a portfolio.")
        tk.Label(
            title_box, textvariable=self.ticket_heading_var, bg=PALETTE["surface"],
            fg=PALETTE["ink"], font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")
        tk.Label(
            title_box, textvariable=self.ticket_subheading_var, bg=PALETTE["surface"],
            fg=PALETTE["soft_ink"], font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))

        tools = tk.Frame(toolbar, bg=PALETTE["surface"])
        tools.pack(side="right")
        self.copy_all_button = self._workspace_button(tools, "Copy all", self.copy_all_tickets, subtle=True)
        self.copy_all_button.pack(side="left", padx=(0, 7))
        self.clear_button = self._workspace_button(tools, "Clear", self.clear_generated_tickets, subtle=True)
        self.clear_button.pack(side="left", padx=(0, 7))
        self.regenerate_button = self._workspace_button(tools, "Regenerate", self.generate, subtle=True)
        self.regenerate_button.pack(side="left", padx=(0, 7))
        self.workspace_save_button = self._workspace_button(tools, "Save", self.save_tickets)
        self.workspace_save_button.pack(side="left")

        self.summary_frame = tk.Frame(frame, bg=PALETTE["surface"])
        self.summary_frame.pack(fill="x", padx=20, pady=(0, 12))
        self.summary_vars = {
            "lines": tk.StringVar(value="—"),
            "covered": tk.StringVar(value="—"),
            "special": tk.StringVar(value="—"),
            "pairs": tk.StringVar(value="—"),
            "triples": tk.StringVar(value="—"),
            "overlap": tk.StringVar(value="—"),
        }
        self.summary_label_vars = {
            "covered": tk.StringVar(value="Main numbers covered"),
            "special": tk.StringVar(value="Special balls covered"),
        }
        summary_items = [
            ("Lines generated", "lines"),
            (self.summary_label_vars["covered"], "covered"),
            (self.summary_label_vars["special"], "special"),
            ("Pair coverage", "pairs"),
            ("Triple coverage", "triples"),
            ("Avg. main overlap", "overlap"),
        ]
        for index, (label, key) in enumerate(summary_items):
            card = tk.Frame(
                self.summary_frame, bg="#F8FAFC", highlightthickness=1,
                highlightbackground="#D9E3EE"
            )
            card.pack(side="left", fill="x", expand=True, padx=(0 if index == 0 else 5, 0))
            tk.Label(
                card, textvariable=self.summary_vars[key], bg="#F8FAFC", fg=PALETTE["ink"],
                font=("Segoe UI", 15, "bold"), pady=5
            ).pack()
            label_kwargs = {"textvariable": label} if isinstance(label, tk.StringVar) else {"text": label}
            tk.Label(
                card, **label_kwargs, bg="#F8FAFC", fg=PALETTE["soft_ink"],
                font=("Segoe UI", 8, "bold"), pady=3
            ).pack()

        body = tk.Frame(frame, bg="#F7F9FC")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        self.ticket_canvas = tk.Canvas(
            body, bg="#F7F9FC", highlightthickness=0, bd=0, takefocus=True
        )
        self.ticket_scroll = ttk.Scrollbar(
            body, orient="vertical", command=self.ticket_canvas.yview, style="DrawWise.Vertical.TScrollbar"
        )
        self.ticket_canvas.configure(yscrollcommand=self.ticket_scroll.set)
        self.ticket_canvas.pack(side="left", fill="both", expand=True)
        self.ticket_scroll.pack(side="right", fill="y")
        self.ticket_cards = tk.Frame(self.ticket_canvas, bg="#F7F9FC")
        self.ticket_window = self.ticket_canvas.create_window((0, 0), window=self.ticket_cards, anchor="nw")
        self.ticket_cards.bind(
            "<Configure>", lambda _e: self.ticket_canvas.configure(scrollregion=self.ticket_canvas.bbox("all"))
        )
        self.ticket_canvas.bind(
            "<Configure>", lambda e: self.ticket_canvas.itemconfigure(self.ticket_window, width=e.width)
        )
        self.ticket_canvas.bind("<MouseWheel>", self._on_ticket_mousewheel)
        self._render_empty_tickets()

    def _workspace_button(self, parent, text, command, subtle=False):
        bg = "#E8EEF6" if subtle else "#2563EB"
        fg = PALETTE["ink"] if subtle else "#FFFFFF"
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground="#D8E8FF" if subtle else "#1D4ED8",
            activeforeground=PALETTE["ink"] if subtle else "#FFFFFF",
            relief="flat", bd=0, cursor="hand2", takefocus=True,
            highlightthickness=2, highlightbackground=bg, highlightcolor=PALETTE["blue_bright"],
            font=("Segoe UI", 9, "bold"), padx=12, pady=8
        )

    def _on_ticket_mousewheel(self, event):
        try:
            self.ticket_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _clear_ticket_cards(self):
        for child in self.ticket_cards.winfo_children():
            child.destroy()

    def _render_empty_tickets(self):
        self._clear_ticket_cards()
        empty = tk.Frame(
            self.ticket_cards, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D9E3EE"
        )
        empty.pack(fill="x", padx=14, pady=18)
        tk.Label(
            empty, text="No tickets generated yet", bg="#FFFFFF", fg=PALETTE["ink"],
            font=("Segoe UI", 15, "bold")
        ).pack(pady=(34, 6))
        tk.Label(
            empty,
            text="Select a game and strategy on the left, then use Generate tickets. Your lines will appear here as individual cards.",
            bg="#FFFFFF", fg=PALETTE["soft_ink"], font=("Segoe UI", 10),
            wraplength=700, justify="center"
        ).pack(padx=20, pady=(0, 12))
        self._workspace_button(empty, "Generate tickets", self.generate).pack(pady=(0, 34))

    def clear_generated_tickets(self, *, quiet: bool = False):
        """Clear the current generated portfolio without deleting historical data."""
        self.last_result = None
        self.last_ticket_report = ""
        self._backtest_thread = None
        self._backtest_cancel = threading.Event()
        self._backtest_queue = queue.Queue()
        self._backtest_started_at = None
        if hasattr(self, "summary_vars"):
            for value in self.summary_vars.values():
                value.set("—")
        if hasattr(self, "ticket_heading_var"):
            self.ticket_heading_var.set("Your tickets")
            self.ticket_subheading_var.set("Choose your game and strategy, then generate a portfolio.")
        if hasattr(self, "ticket_cards"):
            self._render_empty_tickets()
            self.ticket_canvas.yview_moveto(0)
        if not quiet:
            self._show_view("tickets")
            self.footer_var.set("Generated tickets cleared")

    def _ball_widget(self, parent, number: int, fill: str, text_fill: str = "#FFFFFF", size: int = 42):
        canvas = tk.Canvas(parent, width=size, height=size, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        pad = 2
        canvas.create_oval(pad, pad, size-pad, size-pad, fill=fill, outline=fill)
        canvas.create_text(size/2, size/2, text=f"{number:02d}", fill=text_fill, font=("Segoe UI", 10, "bold"))
        return canvas

    def _render_ticket_dashboard(self, result):
        cfg = result.config
        accent = GAME_ACCENTS.get(cfg.key, "#2563EB")
        self._clear_ticket_cards()
        self.summary_vars["lines"].set(str(len(result.tickets)))
        self.summary_vars["covered"].set(str(len(result.base_pool)))
        if cfg.special_pick:
            covered_special = len(set().union(*(set(t.special) for t in result.tickets))) if result.tickets else 0
            self.summary_vars["special"].set(f"{covered_special}/{cfg.special_range_size}")
            self.summary_label_vars["special"].set(f"{cfg.special_name} covered")
        else:
            self.summary_vars["special"].set("—")
            self.summary_label_vars["special"].set("No special selection")
        self.summary_label_vars["covered"].set("Main numbers covered")
        self.summary_vars["pairs"].set(f"{result.metrics['pair_coverage']:.1%}")
        self.summary_vars["triples"].set(f"{result.metrics['triple_coverage']:.1%}")
        self.summary_vars["overlap"].set(f"{result.metrics['avg_overlap']:.2f}")
        self.ticket_heading_var.set(f"{cfg.name} ticket portfolio")
        purchase_rounds = current_purchase_rounds(cfg)
        odds_suffix = " per purchased line"
        if purchase_rounds > 1:
            odds_suffix += f" across {purchase_rounds} rounds"
        self.ticket_subheading_var.set(
            f"{self.strategy_var.get()}  •  {len(result.draws)} analysis-era draws  •  {result.top_prize_odds}{odds_suffix}"
        )

        info = tk.Frame(
            self.ticket_cards, bg="#EEF5FF", highlightthickness=1, highlightbackground="#C7DCF7"
        )
        info.pack(fill="x", padx=12, pady=(10, 8))
        tk.Frame(info, bg=accent, width=5).pack(side="left", fill="y")
        info_text = f"Distinct lines: {len({(t.main, t.special) for t in result.tickets})}"
        if result.portfolio_probability:
            info_text += f"  •  Exact portfolio jackpot chance: {format_odds(1.0 / result.portfolio_probability)}"
        tk.Label(
            info, text=info_text, bg="#EEF5FF", fg="#17365D",
            font=("Segoe UI", 9, "bold"), padx=12, pady=9
        ).pack(side="left")

        for index, ticket in enumerate(result.tickets, start=1):
            card = tk.Frame(
                self.ticket_cards, bg="#FFFFFF", highlightthickness=1, highlightbackground="#D9E3EE"
            )
            card.pack(fill="x", padx=12, pady=6)
            tk.Frame(card, bg=accent, width=5).pack(side="left", fill="y")

            badge = tk.Frame(card, bg="#F4F7FB", width=74)
            badge.pack(side="left", fill="y")
            badge.pack_propagate(False)
            tk.Label(
                badge, text=f"LINE {index:02d}", bg="#F4F7FB", fg=PALETTE["soft_ink"],
                font=("Segoe UI", 8, "bold")
            ).pack(expand=True)

            number_area = tk.Frame(card, bg="#FFFFFF")
            number_area.pack(side="left", fill="both", expand=True, padx=16, pady=12)
            balls = tk.Frame(number_area, bg="#FFFFFF")
            balls.pack(anchor="w")
            for number in ticket.main:
                ball = self._ball_widget(balls, number, accent)
                ball.pack(side="left", padx=(0, 8))

            if ticket.special:
                sep = tk.Frame(balls, bg="#D9E3EE", width=1, height=36)
                sep.pack(side="left", padx=(7, 12), pady=3)
                special_label = cfg.special_name or "Special"
                tk.Label(
                    balls, text=special_label, bg="#FFFFFF", fg=PALETTE["soft_ink"],
                    font=("Segoe UI", 8, "bold")
                ).pack(side="left", padx=(0, 8))
                special_fill, special_text = SPECIAL_STYLES.get(special_label, ("#0E7490", "#FFFFFF"))
                for number in ticket.special:
                    ball = self._ball_widget(balls, number, special_fill, special_text)
                    ball.pack(side="left", padx=(0, 8))

            tk.Label(
                number_area,
                text=f"{self.strategy_var.get()} • {cfg.main_pick} main number{'s' if cfg.main_pick != 1 else ''}" +
                     (f" + {cfg.special_pick} {cfg.special_name}" if cfg.special_pick else ""),
                bg="#FFFFFF", fg="#64748B", font=("Segoe UI", 8)
            ).pack(anchor="w", pady=(7, 0))

            copy_button = self._workspace_button(
                card, "Copy", lambda t=ticket: self.copy_ticket(t), subtle=True
            )
            copy_button.pack(side="right", padx=14, pady=14)

        if result.guarantees:
            guarantee = tk.Frame(
                self.ticket_cards, bg="#FFF8E6", highlightthickness=1, highlightbackground="#F1D59B"
            )
            guarantee.pack(fill="x", padx=12, pady=(8, 14))
            tk.Label(
                guarantee, text="Wheel guarantees", bg="#FFF8E6", fg="#7A4B00",
                font=("Segoe UI", 10, "bold")
            ).pack(anchor="w", padx=12, pady=(10, 3))
            for k, g in sorted(result.guarantees.items()):
                tk.Label(
                    guarantee,
                    text=f"If {k} winning main numbers are inside the base pool, at least {g} are guaranteed together on one generated line.",
                    bg="#FFF8E6", fg="#694B1B", font=("Segoe UI", 9), justify="left", wraplength=900
                ).pack(anchor="w", padx=12, pady=2)
            tk.Frame(guarantee, bg="#FFF8E6", height=7).pack()

        self.ticket_canvas.yview_moveto(0)

    def copy_ticket(self, ticket):
        cfg = self._config()
        text = ticket.display(cfg.special_name)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.footer_var.set("Ticket copied to clipboard")

    def copy_all_tickets(self):
        if not self.last_result:
            messagebox.showinfo("Nothing to copy", "Generate tickets first.")
            return
        cfg = self.last_result.config
        text = "\n".join(
            f"{i:02d}. {ticket.display(cfg.special_name)}"
            for i, ticket in enumerate(self.last_result.tickets, start=1)
        )
        self.clipboard_clear()
        self.clipboard_append(text)
        self.footer_var.set(f"Copied {len(self.last_result.tickets)} tickets")

    def _bind_shortcuts(self):
        self.bind_all("<Alt-g>", lambda _e: self.generate())
        self.bind_all("<Alt-a>", lambda _e: self.analyse())
        self.bind_all("<Alt-b>", lambda _e: self.backtest())
        self.bind_all("<Alt-l>", lambda _e: self._navigate("strategy_lab"))
        self.bind_all("<Control-d>", lambda _e: self._navigate("data_manager"))
        self.bind_all("<Control-Shift-U>", lambda _e: self._navigate("updater"))
        self.bind_all("<Control-u>", lambda _e: self.update_history())
        self.bind_all("<Control-s>", lambda _e: self.save_tickets())

    # ---------- state ----------
    def _config(self):
        return BY_NAME[self.game_var.get()]

    def _analysis_history_window(self):
        raw = self.analysis_history_window_var.get().strip()
        if not raw or raw.lower() == "all":
            return None
        try:
            return max(1, int(raw))
        except ValueError:
            return None

    @staticmethod
    def _test_horizon(value: str | None):
        raw = (value or "").strip()
        if not raw or raw.lower() == "all":
            return None
        try:
            return max(1, int(raw))
        except ValueError:
            return None

    def _parse_keys(self):
        raw = self.key_var.get().replace(",", " ").strip()
        if not raw:
            return []
        try:
            return [int(x) for x in raw.split()]
        except ValueError as exc:
            raise ValueError("Key numbers must be integers separated by spaces or commas") from exc

    def _on_strategy_changed(self):
        self._update_strategy_help()
        self._update_control_states()
        if self.last_result is not None:
            self.clear_generated_tickets(quiet=True)
            self._show_view("tickets")
            self.footer_var.set("Strategy changed — previous generated tickets cleared")
        self._render_methodology()

    def _update_strategy_help(self):
        self.strategy_help_var.set(STRATEGY_HELP.get(self.strategy_var.get(), ""))

    def _update_control_states(self):
        strategy = self.strategy_var.get()
        cfg = self._config() if self.game_var.get() else None

        # Rebuild the visible settings list instead of leaving irrelevant controls
        # greyed-out. This reduces visual noise and avoids low-contrast disabled fields.
        for row in (self.lines_row, self.pool_row, self.special_row, self.recent_row, self.key_row):
            row.pack_forget()

        if strategy != "Full Wheel":
            self.lines_row.pack(fill="x", pady=5)

        if strategy in POOL_STRATEGIES:
            self.pool_row.pack(fill="x", pady=5)

        if cfg and cfg.special_pick and strategy in SPECIAL_POOL_STRATEGIES:
            self.special_row.pack(fill="x", pady=5)

        # The recent window remains available because it is also used by the
        # Number Analysis and walk-forward evaluation screens.
        self.recent_row.pack(fill="x", pady=5)

        if strategy == "Key Number Wheel":
            self.key_row.pack(fill="x", pady=5)
            self.key_entry.configure(state="normal")
        else:
            self.key_entry.configure(state="normal")

        self.lines_spin.configure(state="normal")
        self.pool_spin.configure(state="normal")
        self.special_spin.configure(state="normal")

        if strategy == "Diversified Smart Portfolio":
            hint = "Broad-coverage mode: base-pool and key-number controls are hidden because this method deliberately spreads across the full number universe."
        elif strategy == "Balanced Random":
            hint = "Random baseline mode: only line count and analysis window are needed."
        elif strategy == "Full Wheel":
            hint = "Full wheel mode: line count is hidden because every complete combination inside the selected pool is generated."
        elif strategy == "Key Number Wheel":
            hint = "Enter key numbers separated by commas, for example 7, 18. They will appear on every main-number line."
        else:
            hint = "Main base pool controls how many ranked candidate numbers are concentrated into this strategy."
        self.control_hint_var.set(hint)

    def on_game_changed(self):
        cfg = self._config()
        had_tickets = self.last_result is not None
        if had_tickets:
            self.clear_generated_tickets(quiet=True)
        if hasattr(self, "game_chip"):
            accent = GAME_ACCENTS.get(cfg.key, "#2563EB")
            self.game_chip.configure(text=cfg.name, fg=accent)
            if hasattr(self, "intro_accent"):
                self.intro_accent.configure(bg=accent)
        self.pool_var.set(cfg.default_pool_size)
        self.special_pool_var.set(cfg.default_special_pool_size)
        self.pool_spin.configure(from_=cfg.main_pick, to=min(18, cfg.main_range_size))
        if cfg.special_pick:
            self.special_spin.configure(from_=cfg.special_pick, to=cfg.special_range_size)
        else:
            self.special_pool_var.set(0)
        self._update_control_states()
        self._refresh_data_status()
        if hasattr(self, "data_manager_tree"):
            self.refresh_data_audit()
        self._render_methodology()
        self._analysis_cache = None
        if hasattr(self, "analysis_tree"):
            for item in self.analysis_tree.get_children():
                self.analysis_tree.delete(item)
            for key in ("draws", "recent", "latest", "range"):
                self.analysis_summary_vars[key].set("—")
            self.analysis_special_var.set("Open Number Analysis to calculate fresh game-aware statistics for the selected game.")
        if hasattr(self, "summary_label_vars"):
            self.summary_label_vars["covered"].set("Main numbers covered")
            self.summary_label_vars["special"].set(f"{cfg.special_name} covered" if cfg.special_pick else "No special selection")
        if hasattr(self, "strategy_lab_tree"):
            self._strategy_lab_last_result = None
            self.strategy_lab_use_button.configure(state="disabled")
            self.strategy_lab_summary_vars["tested"].set("—")
            self.strategy_lab_summary_vars["benchmark"].set("—")
            self.strategy_lab_summary_vars["winner"].set("—")
            self.strategy_lab_summary_vars["lift"].set("—")
            for item in self.strategy_lab_tree.get_children():
                self.strategy_lab_tree.delete(item)
            self.strategy_lab_status.configure(bg="#EEF5FF", fg="#17365D", highlightbackground="#C7DCF7")
            self.strategy_lab_status_var.set("Game changed. Run Strategy Lab to create a fresh side-by-side comparison.")
            self.strategy_lab_progress_var.set(0.0)
            self.strategy_lab_progress_text_var.set("Ready")
        if hasattr(self, "updater_tree"):
            self._refresh_updater_local_rows()
            if cfg.key in self.updater_tree.get_children():
                self.updater_tree.selection_set(cfg.key)
                self.updater_tree.focus(cfg.key)
                self._update_updater_action_states()
        if had_tickets:
            self._show_view("tickets")
            self.footer_var.set(f"Selected game: {cfg.name} — previous tickets cleared")
        else:
            self.footer_var.set(f"Selected game: {cfg.name}")

    def _refresh_data_status(self):
        cfg = self._config()
        try:
            audit = audit_history(cfg.csv_path(self.runtime_root), cfg)
            if audit.earliest and audit.latest:
                clean = cleaned_unique_draws(audit)
                analysis_ready = filter_current_analysis_draws(cfg, clean)
                depth = depth_status(cfg, len(analysis_ready))
                warning = "\nLimited sample — ranking stability is reduced." if len(analysis_ready) < 100 else ""
                quality = f"Quality {audit.quality_score}/100 • {audit.completeness_pct:.0f}% cadence complete"
                self.data_status_var.set(
                    f"Historical database\n{len(analysis_ready)}/{len(clean)} analysis-ready/stored  •  Latest {audit.latest:%d-%b-%Y}\n"
                    f"Depth {len(analysis_ready)}/{depth.target} • {depth.tier}\n{quality}{warning}"
                )
            else:
                self.data_status_var.set("History file has no usable draw dates. Open Data Manager.")
        except Exception as exc:
            self.data_status_var.set(f"History error:\n{exc}")

    def _replace_text(self, widget: tk.Text, text: str):
        previous_state = str(widget.cget("state"))
        if previous_state == "disabled":
            widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.see("1.0")
        if previous_state == "disabled":
            widget.configure(state="disabled")

    # ---------- actions ----------
    def generate(self):
        cfg = self._config()
        try:
            result = self.engine.generate(
                config=cfg,
                strategy=self.strategy_var.get(),
                lines=self.lines_var.get(),
                pool_size=self.pool_var.get(),
                special_pool_size=self.special_pool_var.get(),
                recent_window=self.recent_var.get(),
                key_numbers=self._parse_keys(),
            )
        except Exception as exc:
            messagebox.showerror("Generation error", str(exc))
            return

        self.last_result = result
        unique_count = len({(t.main, t.special) for t in result.tickets})
        self.last_ticket_report = self._build_ticket_report(result)
        self._render_ticket_dashboard(result)
        self._show_view("tickets")
        self.footer_var.set(f"Generated {unique_count} unique {cfg.name} ticket(s)")

    def _build_ticket_report(self, result) -> str:
        cfg = result.config
        strategy = self.strategy_var.get()
        unique_count = len({(t.main, t.special) for t in result.tickets})
        lines = [
            f"{cfg.name.upper()} — {strategy}",
            "=" * 86,
            f"History used       : {len(result.draws)} draws ({result.draws[0].draw_date} to {result.draws[-1].draw_date})",
            (
                f"Top-prize odds    : {result.top_prize_odds} per purchased line"
                + (
                    f" across {current_purchase_rounds(cfg)} current rounds (single-round odds {format_odds(single_round_top_prize_denominator(cfg))})"
                    if current_purchase_rounds(cfg) > 1 else ""
                )
            ),
            f"Unique tickets    : {unique_count}",
            (
                f"Main numbers covered: {result.base_pool}"
                if strategy in {"Diversified Smart Portfolio", "Balanced Random"}
                else f"Main base pool    : {result.base_pool}"
            ),
            "",
            "PORTFOLIO COVERAGE",
            f"Pair coverage     : {result.metrics['pair_coverage']:.1%}",
            f"Triple coverage   : {result.metrics['triple_coverage']:.1%}",
            f"Average overlap   : {result.metrics['avg_overlap']:.2f} main numbers between lines",
        ]
        if cfg.special_pick:
            covered_special = len(set().union(*(set(t.special) for t in result.tickets))) if result.tickets else 0
            lines += [
                f"{cfg.special_name} covered: {covered_special}/{cfg.special_range_size} ({result.metrics['special_coverage']:.1%})",
                f"Average special overlap: {result.metrics['avg_special_overlap']:.2f} special numbers between lines",
            ]
        if result.portfolio_probability:
            lines.append(f"Exact jackpot chance for this portfolio: {format_odds(1.0 / result.portfolio_probability)}")
        if result.guarantees:
            lines += ["", "MAIN-NUMBER WHEEL GUARANTEES"]
            for k, guarantee in sorted(result.guarantees.items()):
                lines.append(
                    f"If {k} winning main number(s) are inside the base pool, every possible such case "
                    f"is guaranteed at least {guarantee} main match(es) on one generated line."
                )
        lines += ["", "GENERATED TICKETS", "-" * 86]
        for i, ticket in enumerate(result.tickets, start=1):
            lines.append(f"{i:03d}. {ticket.display(cfg.special_name)}")
        lines += [
            "",
            "METHOD NOTE",
            "Historical ranking structures selection; it does not alter the physical probability of an independent draw.",
            "Distinct-line coverage and wheel coverage are the parts that change how much of the combination space you own.",
        ]
        if len(result.draws) < 100:
            lines += ["", f"DATA WARNING: only {len(result.draws)} draws are available for {cfg.name}; historical ranking stability is limited."]
        return "\n".join(lines)

    def analyse(self):
        cfg = self._config()
        history_window = self._analysis_history_window()
        try:
            draws, main_stats, special_stats = self.engine.analyze(
                cfg, self.recent_var.get(), history_window=history_window
            )
        except Exception as exc:
            messagebox.showerror("Analysis error", str(exc))
            return

        self._analysis_cache = (cfg, draws, main_stats, special_stats)
        # Always open on main numbers; the user can switch to the separate special-ball universe.
        self._render_analysis_domain("main")
        self._show_view("analysis")
        window_label = "all available" if history_window is None else f"last {len(draws)}"
        self.footer_var.set(f"Analysed {window_label} {cfg.name} draws")

    def _render_analysis_domain(self, domain: str):
        cache = getattr(self, "_analysis_cache", None)
        if not cache:
            return
        cfg, draws, main_stats, special_stats = cache
        if domain == "special" and not special_stats:
            domain = "main"
        stats = special_stats if domain == "special" else main_stats
        self.analysis_domain_var.set(domain)

        # Accessible selected state: colour is reinforced by the button text and focus state.
        active_bg = "#DCEBFF"
        inactive_bg = "#E8EEF6"
        self.analysis_main_button.configure(
            bg=active_bg if domain == "main" else inactive_bg,
            fg="#174EA6" if domain == "main" else PALETTE["ink"],
        )
        if special_stats:
            if not self.analysis_special_button.winfo_manager():
                self.analysis_special_button.pack(side="left")
            self.analysis_special_button.configure(
                text=cfg.special_name or "Special balls",
                bg=active_bg if domain == "special" else inactive_bg,
                fg="#174EA6" if domain == "special" else PALETTE["ink"],
            )
        else:
            self.analysis_special_button.pack_forget()

        recent_window = min(self.recent_var.get(), len(draws))
        self.analysis_summary_vars["draws"].set(str(len(draws)))
        self.analysis_summary_vars["recent"].set(str(recent_window))
        self.analysis_summary_vars["latest"].set(draws[-1].draw_date.strftime("%d-%b-%Y"))
        if domain == "special":
            self.analysis_summary_vars["range"].set(f"{cfg.special_min}–{cfg.special_max}")
        else:
            self.analysis_summary_vars["range"].set(f"{cfg.main_min}–{cfg.main_max}")

        by_frequency = sorted(stats, key=lambda st: (-st.frequency_index, -st.recent_index, st.number))
        by_recent = sorted(stats, key=lambda st: (-st.recent_index, -st.frequency_index, st.number))
        by_gap = sorted(stats, key=lambda st: (-st.gap_draws, st.number))
        self.analysis_band_vars["hot"].set(
            "  •  ".join(f"{st.number:02d} ({st.frequency_index:.2f}×)" for st in by_frequency[:6])
        )
        self.analysis_band_vars["recent"].set(
            "  •  ".join(f"{st.number:02d} ({st.recent_count})" for st in by_recent[:6])
        )
        self.analysis_band_vars["gaps"].set(
            "  •  ".join(f"{st.number:02d} ({st.gap_draws} draws)" for st in by_gap[:6])
        )

        for item in self.analysis_tree.get_children():
            self.analysis_tree.delete(item)
        prefix = "s" if domain == "special" else "n"
        for stat in stats:
            if stat.frequency_index >= 1.20:
                status, tag = "Higher frequency", "hot"
            elif stat.frequency_index <= 0.80:
                status, tag = "Lower frequency", "cold"
            else:
                status, tag = "Near expected", "balanced"
            self.analysis_tree.insert(
                "", "end", iid=f"{prefix}{stat.number}",
                values=(
                    f"{stat.number:02d}", stat.count, stat.recent_count, stat.gap_draws,
                    f"{stat.frequency_index:.2f}×", f"{stat.recent_index:.2f}×",
                    f"{stat.score:.3f}", status,
                ),
                tags=(tag,),
            )

        domain_name = (cfg.special_name or "Special balls") if domain == "special" else "Main numbers"
        sample_note = ""
        if len(draws) < 100:
            sample_note = f"  •  Limited sample: {len(draws)} draws; rankings can move sharply as more history is added."
        self.analysis_special_var.set(
            f"Viewing {domain_name} only. Main and special-ball universes are analysed separately. "
            "Frequency index 1.00× equals the equal-frequency expectation in this sample; none of these statistics makes a number due."
            + sample_note
        )

    def backtest(self):
        """Run the walk-forward backtest off the Tk main thread.

        The calculation can be CPU intensive because each historical target draw is
        evaluated against a generated portfolio plus repeated random benchmarks.
        Keeping that work in a worker thread prevents Windows from marking DrawWise
        as "Not Responding".
        """
        if self._strategy_lab_thread is not None and self._strategy_lab_thread.is_alive():
            self._show_view("strategy_lab")
            self.strategy_lab_status_var.set("Strategy Lab is already running. Finish or cancel it before starting a separate backtest.")
            return
        if self._backtest_thread is not None and self._backtest_thread.is_alive():
            self._show_view("backtest")
            return

        cfg = self._config()
        try:
            params = {
                "config": cfg,
                "strategy": self.strategy_var.get(),
                "lines": self.lines_var.get(),
                "pool_size": self.pool_var.get(),
                "special_pool_size": self.special_pool_var.get(),
                "recent_window": self.recent_var.get(),
                "key_numbers": self._parse_keys(),
                "max_tests": self._test_horizon(self.backtest_horizon_var.get()),
            }
        except Exception as exc:
            messagebox.showerror("Backtest settings", str(exc))
            return

        self._show_view("backtest")
        self._reset_backtest_display_for_run()
        self._backtest_cancel = threading.Event()
        self._backtest_queue = queue.Queue()
        self._backtest_started_at = time.monotonic()
        self._set_backtest_running(True)

        def progress_callback(completed, total, target_date):
            self._backtest_queue.put(("progress", completed, total, target_date))

        def worker():
            try:
                result = self.engine.backtest(
                    **params,
                    progress_callback=progress_callback,
                    cancel_event=self._backtest_cancel,
                )
                self._backtest_queue.put(("result", result, cfg, params))
            except BacktestCancelled:
                self._backtest_queue.put(("cancelled",))
            except Exception as exc:
                self._backtest_queue.put(("error", str(exc)))

        self._backtest_thread = threading.Thread(
            target=worker, name="DrawWiseBacktest", daemon=True
        )
        self._backtest_thread.start()
        self.after(75, self._poll_backtest_queue)

    def _reset_backtest_display_for_run(self):
        for key in ("tested", "strategy", "random", "lift", "best"):
            self.backtest_summary_vars[key].set("—")
        self.backtest_progress_var.set(0.0)
        self.backtest_progress_text_var.set("Starting…")
        self.backtest_status.configure(bg="#EEF5FF", fg="#17365D", highlightbackground="#C7DCF7")
        self.backtest_status_var.set("Running walk-forward backtest in the background…")
        self.backtest_threshold_var.set("Match thresholds will appear here after the test.")
        self._replace_text(
            self.backtest_text,
            "DrawWise is hiding each historical target draw, generating the selected strategy from prior history only, and comparing it with repeated random portfolios.\n\nYou can continue to view this page while the calculation runs, or cancel it safely."
        )

    def _set_backtest_running(self, running: bool):
        self.backtest_run_button.configure(state="disabled" if running else "normal")
        self.backtest_cancel_button.configure(state="normal" if running else "disabled")
        self.backtest_horizon_combo.configure(state="disabled" if running else "readonly")
        # Lock controls that would make an in-flight result ambiguous. Other navigation remains usable.
        state = "disabled" if running else "readonly"
        self.game_combo.configure(state=state)
        self.strategy_combo.configure(state=state)
        spin_state = "disabled" if running else "normal"
        for widget in (self.lines_spin, self.pool_spin, self.special_spin, self.recent_spin):
            widget.configure(state=spin_state)
        self.key_entry.configure(state="disabled" if running else "normal")
        self.generate_button.configure(state="disabled" if running else "normal")

    def cancel_backtest(self):
        if self._backtest_thread is None or not self._backtest_thread.is_alive():
            return
        self._backtest_cancel.set()
        self.backtest_cancel_button.configure(state="disabled")
        self.backtest_status_var.set("Cancelling backtest safely after the current calculation…")
        self.backtest_progress_text_var.set("Cancelling…")

    def _poll_backtest_queue(self):
        terminal_event_seen = False
        try:
            while True:
                event = self._backtest_queue.get_nowait()
                kind = event[0]
                if kind == "progress":
                    _, completed, total, target_date = event
                    pct = 100.0 * completed / total if total else 0.0
                    self.backtest_progress_var.set(pct)
                    elapsed = max(0.0, time.monotonic() - (self._backtest_started_at or time.monotonic()))
                    if completed and total and completed < total:
                        eta = elapsed / completed * (total - completed)
                        timing = f" • about {eta:.0f}s remaining" if eta >= 1 else ""
                    else:
                        timing = ""
                    date_text = target_date.strftime("%d-%b-%Y") if hasattr(target_date, "strftime") else str(target_date)
                    self.backtest_progress_text_var.set(
                        f"{completed}/{total} draws • {pct:.0f}% • testing {date_text}{timing}"
                    )
                elif kind == "result":
                    _, result, cfg, params = event
                    terminal_event_seen = True
                    self._finish_backtest_result(result, cfg, params)
                elif kind == "cancelled":
                    terminal_event_seen = True
                    self._finish_backtest_cancelled()
                elif kind == "error":
                    _, message = event
                    terminal_event_seen = True
                    self._finish_backtest_error(message)
        except queue.Empty:
            pass

        if not terminal_event_seen and self._backtest_thread is not None and self._backtest_thread.is_alive():
            self.after(75, self._poll_backtest_queue)
        elif not terminal_event_seen and self._backtest_thread is not None:
            # Worker exited between queue polls; give its final queue event one more chance.
            self.after(50, self._poll_backtest_queue)

    def _finish_backtest_cancelled(self):
        self._set_backtest_running(False)
        self._update_control_states()
        self.backtest_status.configure(bg="#FFF8E6", fg="#7A4B00", highlightbackground="#F5D58B")
        self.backtest_status_var.set("Backtest cancelled. No partial result has been treated as evidence.")
        self.backtest_progress_text_var.set("Cancelled")
        self._replace_text(self.backtest_text, "Backtest cancelled by user. Run it again whenever you are ready.")
        self.footer_var.set("Backtest cancelled")
        self._backtest_thread = None

    def _finish_backtest_error(self, message):
        self._set_backtest_running(False)
        self._update_control_states()
        self.backtest_status.configure(bg="#FFF1F2", fg="#9F1239", highlightbackground="#FECDD3")
        self.backtest_status_var.set("Backtest could not run.")
        self.backtest_progress_text_var.set("Error")
        self._replace_text(self.backtest_text, message)
        self.footer_var.set("Backtest failed")
        self._backtest_thread = None
        messagebox.showerror("Backtest error", message)

    def _finish_backtest_result(self, result, cfg, params):
        self._set_backtest_running(False)
        self._update_control_states()
        self._backtest_thread = None

        if result.tested_draws == 0:
            self.backtest_summary_vars["tested"].set("0")
            self.backtest_summary_vars["strategy"].set("—")
            self.backtest_summary_vars["random"].set("—")
            self.backtest_summary_vars["lift"].set("—")
            self.backtest_summary_vars["best"].set("—")
            self.backtest_status_var.set(result.notes)
            self.backtest_progress_var.set(0.0)
            self.backtest_progress_text_var.set("Not enough history")
            self.backtest_threshold_var.set("Not enough out-of-sample history to calculate match thresholds.")
            self._replace_text(self.backtest_text, result.notes)
            self.footer_var.set("Backtest needs more history")
            return

        self.backtest_progress_var.set(100.0)
        elapsed = max(0.0, time.monotonic() - (self._backtest_started_at or time.monotonic()))
        self.backtest_progress_text_var.set(f"Complete • {result.tested_draws} draws • {elapsed:.1f}s")
        self.backtest_summary_vars["tested"].set(str(result.tested_draws))
        self.backtest_summary_vars["strategy"].set(f"{result.avg_best_line_hits:.3f}")
        self.backtest_summary_vars["random"].set(f"{result.random_avg_best_line_hits:.3f}")
        self.backtest_summary_vars["lift"].set(f"{result.best_line_lift:+.3f}")
        self.backtest_summary_vars["best"].set(str(result.max_best_line_hits))

        strategy_name = params["strategy"]
        if result.best_line_lift > 0.05:
            status_bg, status_fg = "#E8F7EE", "#166534"
            verdict = (
                f"Historical diagnostic: {strategy_name} finished {result.best_line_lift:+.3f} main matches above the random benchmark on average. "
                "Treat this as sample evidence only, not a forecast advantage."
            )
        elif result.best_line_lift < -0.05:
            status_bg, status_fg = "#FFF1F2", "#9F1239"
            verdict = (
                f"Historical diagnostic: {strategy_name} finished {result.best_line_lift:+.3f} main matches below the random benchmark on average. "
                "This method did not outperform the random baseline in this test window."
            )
        else:
            status_bg, status_fg = "#FFF8E6", "#7A4B00"
            verdict = (
                f"Historical diagnostic: {strategy_name} was broadly similar to the random benchmark ({result.best_line_lift:+.3f}). "
                "There is no meaningful separation in this test window."
            )
        self.backtest_status.configure(bg=status_bg, fg=status_fg, highlightbackground=status_bg)
        self.backtest_status_var.set(verdict)

        threshold_text = "  •  ".join(
            f"{threshold}+ main matches: {count}/{result.tested_draws}"
            for threshold, count in result.threshold_counts
        )
        benchmark_text = (
            f"Strategy beat the per-draw random main-number benchmark on {result.beat_random_draws}/{result.tested_draws} draws; "
            f"it was below it on {result.below_random_draws}/{result.tested_draws}. "
            f"Random main benchmark 95% interval: {result.random_ci_low:.3f}–{result.random_ci_high:.3f}."
        )
        extra_complete = ""
        if cfg.special_pick:
            meaningful_patterns = []
            for label, count in result.pattern_counts:
                try:
                    main_part, special_part = (int(x) for x in label.split("+", 1))
                except Exception:
                    continue
                if main_part >= 2 or special_part >= 1:
                    meaningful_patterns.append(f"{label}: {count}/{result.tested_draws}")
            pattern_text = "  •  ".join(meaningful_patterns[:12]) or "No main/special hit patterns recorded."
            extra_complete = (
                f"\nComplete-ticket diagnostic: avg best matched components {result.avg_best_total_hits:.3f} vs random "
                f"{result.random_avg_best_total_hits:.3f} ({result.total_hit_lift:+.3f}); best complete pattern {result.best_complete_pattern}. "
                f"Average {cfg.special_name} universe coverage: {result.avg_special_coverage:.1%}."
                f"\nPortfolio hit patterns (at least one line exactly matched): {pattern_text}"
            )
        self.backtest_threshold_var.set(threshold_text + "\n" + benchmark_text + extra_complete)

        details = "\n".join([
            f"Game: {cfg.name}",
            f"Strategy: {strategy_name}",
            f"Ticket lines per historical test: {params['lines']}",
            f"Recent-history window: {params['recent_window']} draws",
            f"Test horizon requested: {params.get('max_tests') or 'All available'}",
            f"Elapsed time: {elapsed:.1f} seconds",
            "",
            "BASE-POOL DIAGNOSTIC",
            f"Average winning main balls captured in selected pool: {result.avg_pool_hits:.3f}",
            f"Equal-random pool expectation: {result.expected_pool_hits:.3f}",
            f"Pool difference: {result.pool_lift:+.3f}",
            "",
            "PORTFOLIO DIAGNOSTIC",
            f"Average best main-number match from strategy portfolio: {result.avg_best_line_hits:.3f}",
            f"Random portfolio benchmark: {result.random_avg_best_line_hits:.3f}",
            f"Random main benchmark 95% interval: {result.random_ci_low:.3f} to {result.random_ci_high:.3f}",
            f"Difference: {result.best_line_lift:+.3f}",
            f"Best main-number match observed: {result.max_best_line_hits}",
            "",
        ] + ([
            "COMPLETE-TICKET DIAGNOSTIC",
            f"Average best matched ticket components (main + {cfg.special_name}): {result.avg_best_total_hits:.3f}",
            f"Random complete-ticket component benchmark: {result.random_avg_best_total_hits:.3f}",
            f"Random complete benchmark 95% interval: {result.random_total_ci_low:.3f} to {result.random_total_ci_high:.3f}",
            f"Complete component difference: {result.total_hit_lift:+.3f}",
            f"Best complete historical pattern: {result.best_complete_pattern} (main+special)",
            f"Average {cfg.special_name} universe coverage across strategy portfolios: {result.avg_special_coverage:.1%}",
            "This component score counts matched main and special balls equally for diagnostics; it is not a prize-value scale.",
            "",
            "HIT-PATTERN PRESENCE",
            *[f"{label}: {count}/{result.tested_draws} tested draws" for label, count in result.pattern_counts if "+" in label and (int(label.split("+")[0]) >= 2 or int(label.split("+")[1]) >= 1)],
            "",
        ] if cfg.special_pick else []) + [
            result.notes,
            "",
            "Interpretation: this walk-forward result judges historical process consistency. It does not change the probability mechanics of the next independent draw.",
        ])
        self._replace_text(self.backtest_text, details)
        self.footer_var.set(f"Backtest complete: {result.tested_draws} out-of-sample draw(s)")

    def run_strategy_lab(self):
        """Run a fair side-by-side comparison without blocking the Tk UI."""
        if self._backtest_thread is not None and self._backtest_thread.is_alive():
            self._show_view("backtest")
            self.backtest_status_var.set("A backtest is already running. Finish or cancel it before starting Strategy Lab.")
            return
        if self._strategy_lab_thread is not None and self._strategy_lab_thread.is_alive():
            self._show_view("strategy_lab")
            return

        cfg = self._config()
        try:
            trials = int(self.strategy_lab_trials_var.get())
            if not 10 <= trials <= 200:
                raise ValueError("Random trials per draw must be between 10 and 200")
            params = {
                "config": cfg,
                "strategies": STRATEGY_LAB_STRATEGIES,
                "lines": self.lines_var.get(),
                "pool_size": self.pool_var.get(),
                "special_pool_size": self.special_pool_var.get(),
                "recent_window": self.recent_var.get(),
                "random_trials_per_draw": trials,
                "max_tests": self._test_horizon(self.strategy_lab_horizon_var.get()),
            }
        except Exception as exc:
            messagebox.showerror("Strategy Lab settings", str(exc))
            return

        self._show_view("strategy_lab")
        self._reset_strategy_lab_display_for_run()
        self._strategy_lab_cancel = threading.Event()
        self._strategy_lab_queue = queue.Queue()
        self._strategy_lab_started_at = time.monotonic()
        self._set_strategy_lab_running(True)

        def progress_callback(completed, total, phase, strategy, target_date):
            self._strategy_lab_queue.put(
                ("progress", completed, total, phase, strategy, target_date)
            )

        def worker():
            try:
                result = self.engine.compare_strategies(
                    **params,
                    progress_callback=progress_callback,
                    cancel_event=self._strategy_lab_cancel,
                )
                self._strategy_lab_queue.put(("result", result, cfg, params))
            except BacktestCancelled:
                self._strategy_lab_queue.put(("cancelled",))
            except Exception as exc:
                self._strategy_lab_queue.put(("error", str(exc)))

        self._strategy_lab_thread = threading.Thread(
            target=worker, name="DrawWiseStrategyLab", daemon=True
        )
        self._strategy_lab_thread.start()
        self.after(75, self._poll_strategy_lab_queue)

    def _reset_strategy_lab_display_for_run(self):
        self._strategy_lab_last_result = None
        self._updates_thread = None
        self._updates_queue = queue.Queue()
        self._online_results = {}
        self._online_previews = {}
        self._online_errors = {}
        self._online_source_errors = {}
        self._online_diagnostics = {}
        for key in ("tested", "benchmark", "winner", "lift"):
            self.strategy_lab_summary_vars[key].set("—")
        self.strategy_lab_summary_vars["count"].set(str(len(STRATEGY_LAB_STRATEGIES)))
        for item in self.strategy_lab_tree.get_children():
            self.strategy_lab_tree.delete(item)
        self.strategy_lab_progress_var.set(0.0)
        self.strategy_lab_progress_text_var.set("Starting shared benchmark…")
        self.strategy_lab_status.configure(bg="#EEF5FF", fg="#17365D", highlightbackground="#C7DCF7")
        self.strategy_lab_status_var.set(
            "Running Strategy Lab in the background. The random benchmark is calculated once and reused for every strategy."
        )
        self.strategy_lab_notes_var.set(
            "No winner has been chosen yet. DrawWise will rank only out-of-sample historical performance; this is not a future-draw prediction."
        )
        self.strategy_lab_use_button.configure(state="disabled")

    def _set_strategy_lab_running(self, running: bool):
        self.strategy_lab_run_button.configure(state="disabled" if running else "normal")
        self.strategy_lab_cancel_button.configure(state="normal" if running else "disabled")
        self.strategy_lab_trials_spin.configure(state="disabled" if running else "normal")
        self.strategy_lab_horizon_combo.configure(state="disabled" if running else "readonly")
        self.backtest_run_button.configure(state="disabled" if running else "normal")
        state = "disabled" if running else "readonly"
        self.game_combo.configure(state=state)
        self.strategy_combo.configure(state=state)
        spin_state = "disabled" if running else "normal"
        for widget in (self.lines_spin, self.pool_spin, self.special_spin, self.recent_spin):
            widget.configure(state=spin_state)
        self.key_entry.configure(state="disabled" if running else "normal")
        self.generate_button.configure(state="disabled" if running else "normal")

    def cancel_strategy_lab(self):
        if self._strategy_lab_thread is None or not self._strategy_lab_thread.is_alive():
            return
        self._strategy_lab_cancel.set()
        self.strategy_lab_cancel_button.configure(state="disabled")
        self.strategy_lab_status_var.set("Cancelling Strategy Lab safely after the current calculation…")
        self.strategy_lab_progress_text_var.set("Cancelling…")

    def _poll_strategy_lab_queue(self):
        terminal_event_seen = False
        try:
            while True:
                event = self._strategy_lab_queue.get_nowait()
                kind = event[0]
                if kind == "progress":
                    _, completed, total, phase, strategy, target_date = event
                    pct = 100.0 * completed / total if total else 0.0
                    self.strategy_lab_progress_var.set(pct)
                    elapsed = max(0.0, time.monotonic() - (self._strategy_lab_started_at or time.monotonic()))
                    if completed and total and completed < total:
                        eta = elapsed / completed * (total - completed)
                        timing = f" • ~{eta:.0f}s remaining" if eta >= 1 else ""
                    else:
                        timing = ""
                    date_text = target_date.strftime("%d-%b-%Y") if hasattr(target_date, "strftime") else str(target_date)
                    if phase == "benchmark":
                        phase_text = "shared random benchmark"
                    else:
                        phase_text = strategy
                    self.strategy_lab_progress_text_var.set(
                        f"{pct:.0f}% • {phase_text} • {date_text}{timing}"
                    )
                elif kind == "result":
                    _, result, cfg, params = event
                    terminal_event_seen = True
                    self._finish_strategy_lab_result(result, cfg, params)
                elif kind == "cancelled":
                    terminal_event_seen = True
                    self._finish_strategy_lab_cancelled()
                elif kind == "error":
                    _, message = event
                    terminal_event_seen = True
                    self._finish_strategy_lab_error(message)
        except queue.Empty:
            pass

        if not terminal_event_seen and self._strategy_lab_thread is not None and self._strategy_lab_thread.is_alive():
            self.after(75, self._poll_strategy_lab_queue)
        elif not terminal_event_seen and self._strategy_lab_thread is not None:
            self.after(50, self._poll_strategy_lab_queue)

    def _finish_strategy_lab_cancelled(self):
        self._set_strategy_lab_running(False)
        self._update_control_states()
        self._strategy_lab_thread = None
        self.strategy_lab_status.configure(bg="#FFF8E6", fg="#7A4B00", highlightbackground="#F5D58B")
        self.strategy_lab_status_var.set("Strategy Lab cancelled. No partial ranking has been treated as evidence.")
        self.strategy_lab_progress_text_var.set("Cancelled")
        self.strategy_lab_notes_var.set("Run the comparison again whenever you are ready.")
        self.footer_var.set("Strategy Lab cancelled")

    def _finish_strategy_lab_error(self, message):
        self._set_strategy_lab_running(False)
        self._update_control_states()
        self._strategy_lab_thread = None
        self.strategy_lab_status.configure(bg="#FFF1F2", fg="#9F1239", highlightbackground="#FECDD3")
        self.strategy_lab_status_var.set("Strategy Lab could not complete.")
        self.strategy_lab_progress_text_var.set("Error")
        self.strategy_lab_notes_var.set(message)
        self.footer_var.set("Strategy Lab failed")
        messagebox.showerror("Strategy Lab error", message)

    def _finish_strategy_lab_result(self, result, cfg, params):
        self._set_strategy_lab_running(False)
        self._update_control_states()
        self._strategy_lab_thread = None
        self._strategy_lab_last_result = result

        if result.tested_draws == 0 or not result.rows:
            self.strategy_lab_summary_vars["tested"].set("0")
            self.strategy_lab_summary_vars["benchmark"].set("—")
            self.strategy_lab_summary_vars["winner"].set("—")
            self.strategy_lab_summary_vars["lift"].set("—")
            self.strategy_lab_progress_var.set(0.0)
            self.strategy_lab_progress_text_var.set("Not enough history")
            self.strategy_lab_status_var.set(result.notes)
            self.strategy_lab_notes_var.set(result.notes)
            self.footer_var.set("Strategy Lab needs more history")
            return

        elapsed = max(0.0, time.monotonic() - (self._strategy_lab_started_at or time.monotonic()))
        self.strategy_lab_progress_var.set(100.0)
        self.strategy_lab_progress_text_var.set(
            f"Complete • {result.tested_draws} tests/strategy • {elapsed:.1f}s"
        )
        self.strategy_lab_summary_vars["tested"].set(str(result.tested_draws))
        self.strategy_lab_summary_vars["count"].set(str(len(result.rows)))
        self.strategy_lab_summary_vars["benchmark"].set(f"{result.random_avg_best_line_hits:.3f}")

        winner = result.winner
        if winner is not None:
            self.strategy_lab_summary_vars["winner"].set(winner.strategy)
            self.strategy_lab_summary_vars["lift"].set(f"{winner.best_line_lift:+.3f}")
            self.strategy_lab_use_button.configure(state="normal")
        else:
            self.strategy_lab_summary_vars["winner"].set("No valid result")
            self.strategy_lab_summary_vars["lift"].set("—")
            self.strategy_lab_use_button.configure(state="disabled")

        for item in self.strategy_lab_tree.get_children():
            self.strategy_lab_tree.delete(item)

        rank = 0
        for row in result.rows:
            if row.error:
                tag = "error"
                values = ("—", row.strategy, "—", "—", "—", "—", "—", "—", "—", "—", "Unavailable")
            else:
                rank += 1
                thresholds = dict(row.threshold_counts)
                if winner is row:
                    tag, status = "winner", "Leader"
                elif row.best_line_lift > 0.05:
                    tag, status = "positive", "Above random"
                elif row.best_line_lift < -0.05:
                    tag, status = "negative", "Below random"
                else:
                    tag, status = "neutral", "Similar"
                values = (
                    rank,
                    row.strategy,
                    f"{row.avg_best_line_hits:.3f}",
                    f"{row.best_line_lift:+.3f}",
                    f"{row.total_hit_lift:+.3f}" if cfg.special_pick else "—",
                    f"{row.avg_special_coverage:.0%}" if cfg.special_pick else "—",
                    f"{row.beat_random_draws}/{row.tested_draws}",
                    f"{thresholds.get(3, 0)}/{row.tested_draws}",
                    f"{thresholds.get(4, 0)}/{row.tested_draws}",
                    row.best_complete_pattern if cfg.special_pick else row.max_best_line_hits,
                    status,
                )
            self.strategy_lab_tree.insert("", "end", values=values, tags=(tag,))

        if winner is not None:
            if winner.best_line_lift > 0.05:
                bg, fg = "#E8F7EE", "#166534"
                evidence = "finished above"
            elif winner.best_line_lift < -0.05:
                bg, fg = "#FFF1F2", "#9F1239"
                evidence = "still finished below"
            else:
                bg, fg = "#FFF8E6", "#7A4B00"
                evidence = "was broadly similar to"
            self.strategy_lab_status.configure(bg=bg, fg=fg, highlightbackground=bg)
            complete_note = (
                f" Complete-ticket component difference: {winner.total_hit_lift:+.3f}."
                if cfg.special_pick else ""
            )
            self.strategy_lab_status_var.set(
                f"Historical leader: {winner.strategy}. It {evidence} the shared random benchmark by {winner.best_line_lift:+.3f} main matches on average."
                + complete_note
                + " This ranks this test window only; it is not evidence that future lottery mechanics have changed."
            )
        else:
            self.strategy_lab_status_var.set("No strategy produced a valid comparable result.")

        failed = [row for row in result.rows if row.error]
        failed_note = ""
        if failed:
            failed_note = " Unavailable: " + "; ".join(f"{row.strategy}: {row.error}" for row in failed)
        special_note = ""
        if cfg.special_pick:
            special_note = (
                f" Complete-ticket random component benchmark: {result.random_avg_best_total_hits:.3f} "
                f"(95% interval {result.random_total_ci_low:.3f}–{result.random_total_ci_high:.3f})."
            )
        similarity_note = ""
        if result.similarity_warnings:
            similarity_note = " Portfolio-similarity warning: " + " | ".join(result.similarity_warnings)
        else:
            similarity_note = " Portfolio diagnostic: no pair of compared strategies produced suspiciously similar portfolios across this test window."
        self.strategy_lab_notes_var.set(
            f"Game: {cfg.name} • {params['lines']} lines per portfolio • recent window {params['recent_window']} • "
            f"test horizon {params.get('max_tests') or 'All available'} • "
            f"{result.random_trials_per_draw} random portfolios per hidden draw. {result.notes}"
            + special_note + similarity_note + failed_note
        )
        self.footer_var.set(
            f"Strategy Lab complete: {len(result.rows)} strategies across {result.tested_draws} out-of-sample draws"
        )

    def use_strategy_lab_winner(self):
        result = self._strategy_lab_last_result
        winner = result.winner if result is not None else None
        if winner is None:
            return
        self.strategy_var.set(winner.strategy)
        self._on_strategy_changed()
        self._show_view("tickets")
        self.footer_var.set(f"Selected Strategy Lab leader: {winner.strategy}")

    # ---------- verified online results updater ----------
    def _selected_updater_key(self):
        if hasattr(self, "updater_tree"):
            selection = self.updater_tree.selection()
            if selection:
                return selection[0]
        current_name = self.game_var.get().strip() if hasattr(self, "game_var") else ""
        if current_name in BY_NAME:
            return BY_NAME[current_name].key
        return ALL_GAMES[0].key

    @staticmethod
    def _fmt_date(value):
        return value.strftime("%d-%b-%Y") if value else "—"

    def _refresh_updater_local_rows(self):
        if not hasattr(self, "updater_tree"):
            return
        selected = self._selected_updater_key()
        for item in self.updater_tree.get_children():
            self.updater_tree.delete(item)
        for cfg in ALL_GAMES:
            try:
                local = load_draws(cfg.csv_path(self.runtime_root), cfg)
                local_latest = max((d.draw_date for d in local), default=None)
            except Exception:
                local = []
                local_latest = None
            source = official_source(cfg)
            provider_label = source.provider + (" (derived)" if source.parent_game_key else "")
            preview = self._online_previews.get(cfg.key)
            error = self._online_errors.get(cfg.key)
            result = self._online_results.get(cfg.key)
            if error:
                values = (
                    cfg.name.replace(" (Pick 5)", ""), provider_label, self._fmt_date(local_latest), "—", "—", "—", f"Source error: {error}"
                )
                tag = "error"
            elif preview and result:
                values = (
                    cfg.name.replace(" (Pick 5)", ""), provider_label, self._fmt_date(preview.local_latest),
                    self._fmt_date(preview.online_latest), str(preview.new_records),
                    str(preview.corrected_records + preview.date_normalisations), preview.status,
                )
                tag = "update" if preview.status == "Update available" else "ok"
            else:
                values = (cfg.name.replace(" (Pick 5)", ""), provider_label, self._fmt_date(local_latest), "—", "—", "—", "Not checked")
                tag = "unchecked"
            self.updater_tree.insert("", "end", iid=cfg.key, values=values, tags=(tag,))
        if selected in self.updater_tree.get_children():
            self.updater_tree.selection_set(selected)
            self.updater_tree.focus(selected)
        self._update_updater_summary()
        self._update_updater_action_states()

    def _update_updater_summary(self):
        if not hasattr(self, "updater_summary_vars"):
            return
        checked_feeds = {source_game_key(k) for k in (set(self._online_results) | set(self._online_errors))}
        updates = sum(1 for p in self._online_previews.values() if p.status == "Update available")
        errors = len(self._online_source_errors)
        self.updater_summary_vars["sources"].set(f"{source_feed_count()} / {len(ALL_GAMES)}")
        self.updater_summary_vars["checked"].set(str(len(checked_feeds)))
        self.updater_summary_vars["updates"].set(str(updates))
        self.updater_summary_vars["errors"].set(str(errors))

    def _update_updater_action_states(self):
        if not hasattr(self, "updater_import_btn"):
            return
        key = self._selected_updater_key()
        preview = self._online_previews.get(key)
        can_import = bool(preview and preview.status == "Update available")
        self.updater_import_btn.configure(state="normal" if can_import else "disabled")
        diag = self._online_diagnostics.get(key)
        if hasattr(self, "updater_save_diagnostic_btn"):
            self.updater_save_diagnostic_btn.configure(
                state="normal" if (diag and getattr(diag, "diagnostic_bytes", None)) else "disabled"
            )
        try:
            source = official_source(BY_KEY[key])
            detail = source.note
            if key in self._online_errors:
                detail += "  Last check: " + str(self._online_errors[key])
                if diag and getattr(diag, "diagnostic_detail", ""):
                    detail += "  Diagnostic: " + diag.diagnostic_detail
            elif preview:
                result = self._online_results.get(key)
                parser = f" • Parser: {result.parser_used}" if result and result.parser_used else ""
                detail += (
                    f"  Local latest: {self._fmt_date(preview.local_latest)} • Official latest: {self._fmt_date(preview.online_latest)}"
                    f" • New: {preview.new_records} • Corrected: {preview.corrected_records}"
                    f" • Date normalisations: {preview.date_normalisations}{parser}."
                )
            else:
                detail += "  This source has not been checked during the current session."
            self.updater_detail_var.set(detail)
        except Exception:
            pass

    def open_updater_and_check_selected(self):
        self._show_view("updater")
        if self._config().key in self.updater_tree.get_children():
            self.updater_tree.selection_set(self._config().key)
            self.updater_tree.focus(self._config().key)
        self.check_selected_game_online()

    def check_selected_game_online(self):
        self._start_online_check([self._config().key])

    def check_all_games_online(self):
        self._start_online_check(list(RESULT_FEED_KEYS))

    def _start_online_check(self, game_keys):
        if self._updates_thread and self._updates_thread.is_alive():
            messagebox.showinfo("Results updater", "An official-results check is already running.")
            return

        requested = list(dict.fromkeys(game_keys))
        feed_keys = list(dict.fromkeys(source_game_key(k) for k in requested))
        affected = set(feed_keys)
        for feed in feed_keys:
            affected.update(DERIVED_CHILDREN.get(feed, ()))
        for key in affected:
            self._online_results.pop(key, None)
            self._online_previews.pop(key, None)
            self._online_errors.pop(key, None)
            self._online_diagnostics.pop(key, None)
        for feed in feed_keys:
            self._online_source_errors.pop(feed, None)

        self.updater_check_selected_btn.configure(state="disabled")
        self.updater_check_all_btn.configure(state="disabled")
        self.updater_import_btn.configure(state="disabled")
        if hasattr(self, "updater_save_diagnostic_btn"):
            self.updater_save_diagnostic_btn.configure(state="disabled")
        self.updater_status_var.set(
            f"Checking {len(feed_keys)} result source(s) for {len(affected)} supported game history view(s)… Local history remains unchanged."
        )
        self.footer_var.set("Checking official results…")
        self._updates_queue = queue.Queue()

        def worker():
            cache = {}
            for index, feed_key in enumerate(feed_keys, start=1):
                cfg = BY_KEY[feed_key]
                try:
                    result = fetch_official_results(cfg, registry=BY_KEY, cache=cache, timeout=20)
                    self._updates_queue.put(("result", feed_key, result))
                    for child_key in DERIVED_CHILDREN.get(feed_key, ()):
                        child_result = derive_result_for_game(BY_KEY[child_key], result)
                        self._updates_queue.put(("result", child_key, child_result))
                except Exception as exc:
                    self._updates_queue.put(("error", feed_key, exc))
                    for child_key in DERIVED_CHILDREN.get(feed_key, ()):
                        self._updates_queue.put(("derived_error", child_key, feed_key, exc))
                self._updates_queue.put(("progress", index, len(feed_keys), cfg.name))
            self._updates_queue.put(("done", len(feed_keys)))

        self._updates_thread = threading.Thread(target=worker, daemon=True, name="DrawWiseResultsUpdater")
        self._updates_thread.start()
        self.after(100, self._poll_online_updates)

    def _poll_online_updates(self):
        done = False
        try:
            while True:
                event = self._updates_queue.get_nowait()
                kind = event[0]
                if kind == "result":
                    _, key, result = event
                    cfg = BY_KEY[key]
                    try:
                        local = load_draws(cfg.csv_path(self.runtime_root), cfg) if cfg.csv_path(self.runtime_root).exists() else []
                        preview = preview_official_update(cfg, local, result)
                        self._online_results[key] = result
                        self._online_previews[key] = preview
                    except Exception as exc:
                        self._online_errors[key] = f"Local comparison failed: {exc}"
                elif kind == "error":
                    _, key, exc = event
                    self._online_errors[key] = str(exc)
                    self._online_source_errors[source_game_key(key)] = str(exc)
                    if isinstance(exc, OnlineUpdateError):
                        self._online_diagnostics[key] = exc
                elif kind == "derived_error":
                    _, key, feed_key, exc = event
                    self._online_errors[key] = f"Parent source {BY_KEY[feed_key].name}: {exc}"
                    if isinstance(exc, OnlineUpdateError):
                        self._online_diagnostics[key] = exc
                elif kind == "progress":
                    _, completed, total, name = event
                    self.updater_status_var.set(f"Checked {completed}/{total}: {name}")
                elif kind == "done":
                    done = True
        except queue.Empty:
            pass

        self._refresh_updater_local_rows()
        if done:
            self.updater_check_selected_btn.configure(state="normal")
            self.updater_check_all_btn.configure(state="normal")
            updates = sum(1 for p in self._online_previews.values() if p.status == "Update available")
            errors = len(self._online_source_errors)
            checked = len({source_game_key(k) for k in (set(self._online_results) | set(self._online_errors))})
            self.updater_status_var.set(
                f"Official-results check complete. Sources checked: {checked}/{source_feed_count()}. "
                f"Game updates available: {updates}. Source errors: {errors}. Nothing has been imported automatically."
            )
            self.footer_var.set("Official-results check complete")
            self._updates_thread = None
            self._update_updater_action_states()
            return
        if self._updates_thread and self._updates_thread.is_alive():
            self.after(120, self._poll_online_updates)

    def import_selected_online_update(self):
        key = self._selected_updater_key()
        cfg = BY_KEY[key]
        result = self._online_results.get(key)
        preview = self._online_previews.get(key)
        if result is None or preview is None:
            messagebox.showinfo("Results updater", "Check this game online first.")
            return
        if preview.status != "Update available":
            messagebox.showinfo("Results updater", f"{cfg.name} does not currently have a pending checked update.")
            return
        destination = cfg.csv_path(self.runtime_root)
        existing = load_draws(destination, cfg) if destination.exists() else []
        try:
            merged, preview = merge_official_update(cfg, existing, result)
        except Exception as exc:
            messagebox.showerror("Official update rejected", str(exc))
            return
        prompt = (
            f"OFFICIAL SOURCE: {result.provider}\n"
            f"Game: {cfg.name}\n"
            f"Official latest: {self._fmt_date(preview.online_latest)}\n"
            f"Local latest: {self._fmt_date(preview.local_latest)}\n\n"
            f"New records: {preview.new_records}\n"
            f"Corrected records: {preview.corrected_records}\n"
            f"Official-date normalisations: {preview.date_normalisations}\n"
            f"Local records after merge: {preview.final_rows}\n\n"
            "DrawWise will back up the current local history before writing. Import this checked update?"
        )
        if not messagebox.askyesno(f"Import checked {cfg.name} update", prompt):
            return
        backup_dir = user_root() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{cfg.key}_official_{datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            if destination.exists():
                shutil.copy2(destination, backup)
            write_draws(destination, cfg, merged)
        except Exception as exc:
            messagebox.showerror("Official update failed", str(exc))
            return
        self._analysis_cache = None
        self._data_audit_cache = None
        self._online_results.pop(key, None)
        self._online_previews.pop(key, None)
        self._online_errors.pop(key, None)
        self._online_diagnostics.pop(key, None)
        self._refresh_data_status()
        if hasattr(self, "data_manager_tree"):
            self.refresh_data_audit()
        self._refresh_updater_local_rows()
        self.footer_var.set(f"Imported checked official update for {cfg.name}")
        messagebox.showinfo(
            "Official results imported",
            f"{cfg.name} local history was updated after your approval.\n\n"
            f"Backup: {backup}\n"
            f"New records: {preview.new_records}\n"
            f"Corrected/date-normalised: {preview.corrected_records + preview.date_normalisations}",
        )

    def save_selected_source_diagnostic(self):
        key = self._selected_updater_key()
        exc = self._online_diagnostics.get(key)
        raw = getattr(exc, "diagnostic_bytes", None) if exc else None
        if not raw:
            messagebox.showinfo("Source diagnostic", "No raw source diagnostic is available for the selected row.")
            return
        extension = getattr(exc, "diagnostic_extension", ".txt") or ".txt"
        suggested = f"drawwise_{source_game_key(key)}_source_diagnostic_{datetime.now():%Y%m%d_%H%M%S}{extension}"
        path = filedialog.asksaveasfilename(
            title="Save source diagnostic",
            initialfile=suggested,
            defaultextension=extension,
            filetypes=[("Diagnostic response", f"*{extension}"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_bytes(raw)
            detail_path = Path(path).with_suffix(Path(path).suffix + ".txt")
            detail_path.write_text(
                f"DrawWise 4.1 source diagnostic\nGame: {BY_KEY[key].name}\nURL: {getattr(exc, 'diagnostic_url', '')}\n"
                f"Error: {exc}\nDetail: {getattr(exc, 'diagnostic_detail', '')}\n",
                encoding="utf-8",
            )
            self.footer_var.set(f"Saved source diagnostic for {BY_KEY[key].name}")
            messagebox.showinfo("Source diagnostic saved", f"Saved raw response:\n{path}\n\nDiagnostic notes:\n{detail_path}")
        except Exception as save_exc:
            messagebox.showerror("Source diagnostic", str(save_exc))

    def open_selected_official_source(self):
        key = self._selected_updater_key()
        cfg = BY_KEY[key]
        try:
            source = official_source(cfg)
            webbrowser.open(source.page_url)
            self.footer_var.set(f"Opened official source for {cfg.name}")
        except Exception as exc:
            messagebox.showerror("Open official source", str(exc))

    def refresh_data_audit(self):
        cfg = self._config()
        path = cfg.csv_path(self.runtime_root)
        try:
            audit = audit_history(path, cfg)
        except Exception as exc:
            messagebox.showerror("Data audit", str(exc))
            return

        self._data_audit_cache = audit
        clean_draws = cleaned_unique_draws(audit)
        analysis_draws = filter_current_analysis_draws(cfg, clean_draws)
        depth = depth_status(cfg, len(analysis_draws))
        self.data_manager_summary_vars["quality"].set(f"{audit.quality_score}/100")
        self.data_manager_summary_vars["valid"].set(f"{len(analysis_draws)}/{len(clean_draws)}")
        self.data_manager_summary_vars["depth"].set(f"{len(analysis_draws)}/{depth.target}")
        self.data_manager_summary_vars["completeness"].set(f"{audit.completeness_pct:.1f}%")
        self.data_manager_summary_vars["duplicates"].set(str(len(audit.duplicate_dates)))
        self.data_manager_summary_vars["gaps"].set(str(len(audit.suspected_missing_dates)))

        if audit.quality_score >= 95 and audit.invalid_rows == 0 and not audit.duplicate_dates:
            bg, fg, border = "#E8F7EE", "#166534", "#B7DFC5"
            headline = "Data quality is strong."
        elif audit.quality_score >= 80:
            bg, fg, border = "#FFF8E6", "#7C4A03", "#F0D79A"
            headline = "Data quality needs review before relying heavily on long-history comparisons."
        else:
            bg, fg, border = "#FFF1F2", "#991B1B", "#F3C4C8"
            headline = "Data quality is weak. Fix invalid rows or structural gaps before treating analysis as reliable."
        self.data_manager_status.configure(bg=bg, fg=fg, highlightbackground=border)
        self.data_manager_status_var.set(
            f"{headline}  Valid rows: {audit.valid_rows}/{audit.total_rows or 0} • "
            f"Analysis-ready/stored: {len(analysis_draws)}/{len(clean_draws)} • Depth: {depth.tier} ({depth.percent:.0f}% of {depth.target}-draw target) • "
            f"Inferred cadence completeness: {audit.completeness_pct:.1f}%."
        )

        earliest = audit.earliest.strftime("%d-%b-%Y") if audit.earliest else "—"
        latest = audit.latest.strftime("%d-%b-%Y") if audit.latest else "—"
        special_obs = ""
        if cfg.special_pick:
            special_obs = f" • Observed {cfg.special_name}: {audit.observed_special_label}"
        self.data_profile_var.set(
            f"Configured game shape: {audit.configured_shape}  •  Observed main range: {audit.observed_main_label}{special_obs}\n"
            f"History range: {earliest} → {latest}  •  Inferred draw weekdays: {audit.weekday_label}  •  "
            f"Median gap: {audit.median_gap_days:.1f} days  •  Maximum gap: {audit.max_gap_days} days"
        )
        available = len(analysis_draws)
        parts = []
        for label, size in (("20", 20), ("50", 50), ("100", 100), ("250", 250)):
            parts.append(f"Last {label}: {'available' if available >= size else f'needs {size - available} more'}")
        parts.append(f"All: {available} records")
        self.data_windows_var.set("Analysis windows  •  " + "  •  ".join(parts))

        main_windows = ranking_windows(analysis_draws, cfg, recent_window=min(20, max(1, len(analysis_draws))), domain="main")
        main_bits = []
        for row in main_windows:
            if row.label == "All":
                continue
            main_bits.append(f"Last {row.label}: {row.overlap_label}" if row.available else f"Last {row.label}: unavailable")
        stability_text = "Main-ranking stability (top 8 overlap with All): " + " • ".join(main_bits)
        if cfg.special_pick:
            special_windows = ranking_windows(analysis_draws, cfg, recent_window=min(20, max(1, len(analysis_draws))), domain="special")
            special_bits = []
            for row in special_windows:
                if row.label == "All":
                    continue
                special_bits.append(f"Last {row.label}: {row.overlap_label}" if row.available else f"Last {row.label}: unavailable")
            stability_text += "\n" + f"{cfg.special_name} stability (top 8 overlap with All): " + " • ".join(special_bits)
        if depth.remaining:
            stability_text += f"\nDepth status: {depth.tier}. Add {depth.remaining} verified draw record(s) to reach the {depth.target}-draw target."
        else:
            stability_text += f"\nDepth status: {depth.tier}. The {depth.target}-draw target has been reached."
        self.data_stability_var.set(stability_text)
        self.data_era_var.set(era_summary(cfg, clean_draws))

        for item in self.data_manager_tree.get_children():
            self.data_manager_tree.delete(item)
        for idx, issue in enumerate(audit.issues):
            where_parts = []
            if issue.draw_date:
                where_parts.append(issue.draw_date.strftime("%d-%b-%Y"))
            if issue.row_number:
                where_parts.append(f"row {issue.row_number}")
            where = " / ".join(where_parts) or "—"
            tag = issue.severity.lower()
            if tag not in {"ok", "review", "warning", "error"}:
                tag = "review"
            self.data_manager_tree.insert(
                "", "end", iid=f"dq{idx}",
                values=(issue.severity, issue.category, where, issue.detail),
                tags=(tag,),
            )
        self.footer_var.set(f"Audited {cfg.name} history: quality {audit.quality_score}/100")

    def show_rule_era_guide(self):
        cfg = self._config()
        try:
            audit = audit_history(cfg.csv_path(self.runtime_root), cfg)
            draws = cleaned_unique_draws(audit)
            profile = rule_profile(cfg)
            lines = [
                f"{cfg.name} — rule-era guide",
                "",
                f"Current analysis universe: {profile.current_analysis_label}",
            ]
            if profile.current_analysis_start:
                lines.append(f"Current analysis start: {profile.current_analysis_start:%d-%b-%Y}")
            lines.append(f"Current purchased-line rounds: {current_purchase_rounds(cfg)}")
            lines.append("")
            lines.extend(era_detail_lines(cfg, draws))
            lines += [
                "",
                "Policy: legacy records may be retained for audit/research, but DrawWise excludes rows from incompatible number universes from current strategy analysis and backtesting.",
            ]
            messagebox.showinfo("Rule-era guide", "\n".join(lines))
        except Exception as exc:
            messagebox.showerror("Rule-era guide", str(exc))

    def export_clean_history(self):
        cfg = self._config()
        audit = audit_history(cfg.csv_path(self.runtime_root), cfg)
        clean = cleaned_unique_draws(audit)
        if not clean:
            messagebox.showerror("Export clean CSV", "No valid draw rows are available to export.")
            return
        path = filedialog.asksaveasfilename(
            title=f"Export cleaned {cfg.name} history",
            initialfile=f"{cfg.key}-clean-history.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            write_draws(Path(path), cfg, clean)
        except Exception as exc:
            messagebox.showerror("Export clean CSV", str(exc))
            return
        self.footer_var.set(f"Exported cleaned history: {Path(path).name}")
        messagebox.showinfo(
            "Clean history exported",
            f"Exported {len(clean)} valid unique draw date(s).\n\n"
            f"Invalid rows excluded: {audit.invalid_rows}\nDuplicate dates resolved: {len(audit.duplicate_dates)}",
        )

    def open_backups_folder(self):
        path = user_root() / "backups"
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Open backups", f"Could not open the backups folder:\n{exc}\n\n{path}")

    def _apply_history_merge(self, cfg, incoming, *, preview_text: str, source_label: str):
        destination = cfg.csv_path(self.runtime_root)
        existing = load_draws(destination, cfg) if destination.exists() else []
        try:
            merged, summary = merge_history(existing, list(incoming))
        except Exception as exc:
            messagebox.showerror("Import rejected", str(exc))
            return False

        prompt = (
            preview_text
            + "\n\nMERGE PREVIEW\n"
            + f"New draw records: {summary.new_records}\n"
            + f"Corrected/replaced records: {summary.replaced_records}\n"
            + f"Already identical: {summary.unchanged_records}\n"
            + f"History after merge: {summary.final_rows} draw record(s).\n\n"
            + "DrawWise will back up the current local history before writing. Apply this update?"
        )
        if not messagebox.askyesno(f"Import {cfg.name} history", prompt):
            return False

        backup_dir = user_root() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{cfg.key}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            if destination.exists():
                shutil.copy2(destination, backup)
            write_draws(destination, cfg, merged)
        except Exception as exc:
            messagebox.showerror("Update failed", str(exc))
            return False

        self._analysis_cache = None
        self._data_audit_cache = None
        self._refresh_data_status()
        if hasattr(self, "data_manager_tree"):
            self.refresh_data_audit()
        self.footer_var.set(
            f"History expanded from {source_label}: +{summary.new_records} new, {summary.replaced_records} corrected"
        )
        messagebox.showinfo(
            "History updated",
            f"{cfg.name} history updated successfully.\n\n"
            f"New records added: {summary.new_records}\n"
            f"Corrected records: {summary.replaced_records}\n"
            f"Total draws: {summary.final_rows}\n\n"
            f"Backup saved in:\n{backup_dir}",
        )
        return True

    def update_history(self):
        """Import one canonical or conservatively aliased results CSV."""
        cfg = self._config()
        source = filedialog.askopenfilename(
            title=f"Select historical/results CSV for {cfg.name}",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not source:
            return

        report = load_flexible_csv(Path(source), cfg)
        if not report.draws:
            details = "\n".join(
                f"{issue.severity}: {issue.detail}" for issue in report.issues[:10]
            ) or "No compatible draw rows were found."
            messagebox.showerror(
                "Results file rejected",
                f"DrawWise could not import usable {cfg.name} rows from this file.\n\n{details}",
            )
            return

        first = report.draws[0].draw_date
        last = report.draws[-1].draw_date
        issue_preview = ""
        if report.rejected_rows:
            samples = "; ".join(
                f"row {i.row_number}: {i.detail}" for i in report.issues[:4] if i.row_number
            )
            issue_preview = f"\nRejected rows: {report.rejected_rows}" + (f"\nExamples: {samples}" if samples else "")
        preview = (
            f"SOURCE FILE: {Path(source).name}\n"
            f"Rows scanned: {report.rows_total}\n"
            f"Accepted rows: {report.accepted_rows}\n"
            f"Import range: {first:%d-%b-%Y} → {last:%d-%b-%Y}\n"
            f"Column mapping: {report.mapping_summary}\n"
            f"Rule-era classification: {era_summary(cfg, report.draws)}"
            + issue_preview
        )
        self._apply_history_merge(cfg, report.draws, preview_text=preview, source_label=Path(source).name)

    def import_history_folder(self):
        """Scan a folder of archive CSVs, normalise compatible files and merge once."""
        cfg = self._config()
        folder = filedialog.askdirectory(title=f"Choose folder containing {cfg.name} archive CSVs")
        if not folder:
            return
        try:
            report = scan_import_folder(Path(folder), cfg)
        except Exception as exc:
            messagebox.showerror("Folder import", str(exc))
            return
        if not report.draws:
            skipped = ", ".join(report.skipped_files[:8]) or "None"
            messagebox.showerror(
                "No compatible history found",
                f"Scanned {report.files_scanned} CSV file(s), but none contained usable {cfg.name} draw rows.\n\n"
                f"Skipped files: {skipped}",
            )
            return

        first = report.draws[0].draw_date
        last = report.draws[-1].draw_date
        warning = ""
        if report.conflicting_overlaps:
            warning = (
                f"\nWARNING: {report.conflicting_overlaps} overlapping draw identity/identities contained different values. "
                "The later scanned record is used in the folder-normalisation stage; verify your archive source before applying."
            )
        preview = (
            f"ARCHIVE FOLDER: {Path(folder).name}\n"
            f"CSV files scanned: {report.files_scanned}\n"
            f"Compatible files used: {report.files_used}\n"
            f"Files skipped: {report.files_skipped}\n"
            f"Rows scanned in compatible files: {report.rows_total}\n"
            f"Accepted rows before de-duplication: {report.accepted_rows}\n"
            f"Rejected rows: {report.rejected_rows}\n"
            f"Normalised unique archive records: {len(report.draws)}\n"
            f"Archive range: {first:%d-%b-%Y} → {last:%d-%b-%Y}\n"
            f"Identical overlaps inside archive: {report.identical_overlaps}\n"
            f"Conflicting overlaps inside archive: {report.conflicting_overlaps}\n"
            f"Rule-era classification: {era_summary(cfg, report.draws)}"
            + warning
        )
        self._apply_history_merge(cfg, report.draws, preview_text=preview, source_label=Path(folder).name)

    def open_data_folder(self):
        path = user_root() / "data"
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Open folder", f"Could not open the data folder:\n{exc}\n\n{path}")

    def save_tickets(self):
        if not self.last_result:
            messagebox.showinfo("Nothing to save", "Generate tickets first.")
            return
        cfg = self.last_result.config
        report_dir = reports_root()
        report_dir.mkdir(parents=True, exist_ok=True)
        default = report_dir / f"{cfg.key}_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path = filedialog.asksaveasfilename(
            title="Save generated tickets",
            initialdir=str(report_dir),
            initialfile=default.name,
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt")],
        )
        if not path:
            return
        content = self.last_ticket_report.rstrip() + "\n"
        Path(path).write_text(content, encoding="utf-8")
        self.footer_var.set(f"Saved: {Path(path).name}")
        messagebox.showinfo("Saved", f"Saved to:\n{path}")

    def _write_methodology(self):
        # Backwards-compatible wrapper retained for any older launcher code.
        self._render_methodology()


def main(start_game: str | None = None):
    if start_game is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--game", choices=list(BY_KEY), default=None)
        args = parser.parse_args()
        start_game = args.game
    app = DrawWiseApp(start_game=start_game)
    app.mainloop()


if __name__ == "__main__":
    main()
