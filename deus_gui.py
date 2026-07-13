"""
deus_gui.py — DEUS 3.0 Lightning Theme GUI
=============================================
Clean, professional dark UI with electric cyan accents.

Layout:
  Top:    Status bar (system health, key indicators)
  Left:   Sidebar (pipeline buttons + agent list)
  Center: Detail panel (agent info, results, output)
  Bottom: Command console (input + scrollable log)

Theme:
  BG:       #0D0D0D (near-black)
  Panel:    #1A1A1A (dark gray)
  Accent:   #00D4FF (electric cyan)
  Text:     #E0E0E0 (light gray)
  Success:  #00FF88
  Error:    #FF4444
  Warning:  #FFAA00
"""

import os
import sys
import json
import time
import threading
import queue
import datetime
import tkinter as tk
from tkinter import scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import Pipeline, list_pipelines, get_available_agents, get_agent_class
from command_processor import CommandProcessor
from base_agent import AgentResult

# ---------------------------------------------------------------------------
# Theme constants
# ---------------------------------------------------------------------------
BG_DARK = "#0D0D0D"
BG_PANEL = "#1A1A1A"
BG_SIDEBAR = "#111111"
BG_CONSOLE = "#0A0A0A"
ACCENT = "#00D4FF"
ACCENT_DIM = "#005F7F"
ACCENT_HOVER = "#00AACC"
TEXT = "#E0E0E0"
TEXT_DIM = "#888888"
TEXT_BRIGHT = "#FFFFFF"
SUCCESS = "#00FF88"
ERROR = "#FF4444"
WARNING = "#FFAA00"
BORDER = "#222222"
BUTTON_BG = "#1E1E1E"
BUTTON_ACTIVE = "#252525"
FONT_FAMILY = "Consolas"
FONT_SIZE = 10
FONT_SIZE_LARGE = 12
FONT_SIZE_TITLE = 14


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class DeusGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DEUS 3.0")
        self.root.geometry("1100x750")
        self.root.minsize(800, 600)
        self.root.configure(bg=BG_DARK)

        self.cmd_processor = CommandProcessor()
        self.log_queue = queue.Queue()
        self.running = False
        self._tip_window = None

        self._build_ui()
        self._refresh_status()
        self._poll_log_queue()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # Status bar (top)
        self._build_status_bar()

        # Main container
        main = tk.Frame(self.root, bg=BG_DARK)
        main.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Sidebar (left)
        self.sidebar = tk.Frame(main, bg=BG_SIDEBAR, width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        # Separator
        sep = tk.Frame(main, bg=BORDER, width=1)
        sep.pack(side=tk.LEFT, fill=tk.Y)

        # Detail panel (center)
        self.detail = tk.Frame(main, bg=BG_PANEL)
        self.detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_detail_panel()

        # Bottom separator
        bsep = tk.Frame(self.root, bg=BORDER, height=1)
        bsep.pack(fill=tk.X)

        # Command console (bottom)
        self._build_console()

    # -- Status bar --

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=BG_PANEL, height=32)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)

        # Title
        tk.Label(bar, text="DEUS 3.0", bg=BG_PANEL, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE_LARGE, "bold")).pack(side=tk.LEFT, padx=10)

        # Status indicators
        self.status_groq = tk.Label(bar, text="Groq: --", bg=BG_PANEL, fg=TEXT_DIM,
                                     font=(FONT_FAMILY, FONT_SIZE))
        self.status_groq.pack(side=tk.LEFT, padx=8)

        self.status_gemini = tk.Label(bar, text="Gemini: --", bg=BG_PANEL, fg=TEXT_DIM,
                                       font=(FONT_FAMILY, FONT_SIZE))
        self.status_gemini.pack(side=tk.LEFT, padx=8)

        self.status_calendly = tk.Label(bar, text="Calendly: --", bg=BG_PANEL, fg=TEXT_DIM,
                                         font=(FONT_FAMILY, FONT_SIZE))
        self.status_calendly.pack(side=tk.LEFT, padx=8)

        # Clock
        self.status_clock = tk.Label(bar, text="", bg=BG_PANEL, fg=TEXT_DIM,
                                      font=(FONT_FAMILY, FONT_SIZE))
        self.status_clock.pack(side=tk.RIGHT, padx=10)
        self._update_clock()

    def _update_clock(self):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.status_clock.config(text=now)
        self.root.after(1000, self._update_clock)

    def _refresh_status(self):
        groq = bool(os.getenv("GROQ_API_KEY", ""))
        gemini = bool(os.getenv("GEMINI_API_KEY", ""))
        calendly = bool(os.getenv("CALENDLY_API_KEY", ""))

        self.status_groq.config(
            text=f"Groq: {'ON' if groq else 'OFF'}",
            fg=SUCCESS if groq else TEXT_DIM,
        )
        self.status_gemini.config(
            text=f"Gemini: {'ON' if gemini else 'OFF'}",
            fg=SUCCESS if gemini else TEXT_DIM,
        )
        self.status_calendly.config(
            text=f"Calendly: {'ON' if calendly else 'OFF'}",
            fg=SUCCESS if calendly else TEXT_DIM,
        )

    # -- Sidebar --

    def _build_sidebar(self):
        # Pipelines section
        tk.Label(self.sidebar, text="PIPELINES", bg=BG_SIDEBAR, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE, "bold"), anchor="w").pack(
            fill=tk.X, padx=10, pady=(12, 4))

        pipelines = list_pipelines()
        for name, info in pipelines.items():
            btn = tk.Button(
                self.sidebar,
                text=f"  {info['name']}",
                bg=BUTTON_BG, fg=TEXT,
                activebackground=BUTTON_ACTIVE, activeforeground=ACCENT,
                font=(FONT_FAMILY, FONT_SIZE),
                anchor="w", relief=tk.FLAT, bd=0,
                cursor="hand2",
                command=lambda n=name: self._run_pipeline_thread(n),
            )
            btn.pack(fill=tk.X, padx=6, pady=1, ipady=4)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=ACCENT_DIM))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=BUTTON_BG))

            # Tooltip
            tip = info.get("description", "")
            if tip:
                btn.bind("<Enter>", lambda e, b=btn, t=tip: self._show_tip(b, t))
                btn.bind("<Leave>", lambda e: self._hide_tip())

        # Agents section
        tk.Label(self.sidebar, text="AGENTS", bg=BG_SIDEBAR, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE, "bold"), anchor="w").pack(
            fill=tk.X, padx=10, pady=(16, 4))

        self.agent_buttons = {}
        agents = get_available_agents()
        for name in agents:
            display = name.replace("_agent", "").replace("_", " ").title()
            btn = tk.Button(
                self.sidebar,
                text=f"  {display}",
                bg=BUTTON_BG, fg=TEXT,
                activebackground=BUTTON_ACTIVE, activeforeground=ACCENT,
                font=(FONT_FAMILY, FONT_SIZE),
                anchor="w", relief=tk.FLAT, bd=0,
                cursor="hand2",
                command=lambda n=name: self._show_agent_detail(n),
            )
            btn.pack(fill=tk.X, padx=6, pady=1, ipady=4)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=ACCENT_DIM))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=BUTTON_BG))
            self.agent_buttons[name] = btn

    # -- Tooltip --

    def _show_tip(self, widget, text):
        self._hide_tip()
        x = widget.winfo_rootx() + widget.winfo_width() + 5
        y = widget.winfo_rooty()
        self._tip_window = tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=ACCENT)
        tk.Label(tw, text=text, bg=ACCENT, fg=BG_DARK,
                 font=(FONT_FAMILY, 8), padx=6, pady=2,
                 wraplength=250, justify="left").pack()

    def _hide_tip(self):
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None

    # -- Detail panel --

    def _build_detail_panel(self):
        # Header
        self.detail_header = tk.Label(self.detail, text="DEUS 3.0", bg=BG_PANEL,
                                       fg=TEXT_BRIGHT, font=(FONT_FAMILY, FONT_SIZE_TITLE, "bold"),
                                       anchor="w")
        self.detail_header.pack(fill=tk.X, padx=16, pady=(12, 2))

        self.detail_subtitle = tk.Label(self.detail, text="Select an agent or pipeline to begin",
                                         bg=BG_PANEL, fg=TEXT_DIM,
                                         font=(FONT_FAMILY, FONT_SIZE), anchor="w")
        self.detail_subtitle.pack(fill=tk.X, padx=16, pady=(0, 8))

        sep = tk.Frame(self.detail, bg=ACCENT, height=2)
        sep.pack(fill=tk.X, padx=16, pady=(0, 8))

        # Scrollable content area
        self.detail_canvas = tk.Canvas(self.detail, bg=BG_PANEL, highlightthickness=0)
        self.detail_scrollbar = tk.Scrollbar(self.detail, orient=tk.VERTICAL,
                                              command=self.detail_canvas.yview)
        self.detail_inner = tk.Frame(self.detail_canvas, bg=BG_PANEL)

        self.detail_inner.bind("<Configure>",
                               lambda e: self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all")))
        self.detail_canvas.create_window((0, 0), window=self.detail_inner, anchor="nw")
        self.detail_canvas.configure(yscrollcommand=self.detail_scrollbar.set)

        self.detail_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bind mousewheel
        self.detail_canvas.bind("<Enter>", lambda e: self.detail_canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.detail_canvas.bind("<Leave>", lambda e: self.detail_canvas.unbind_all("<MouseWheel>"))

        # Welcome content
        self._show_welcome()

    def _on_mousewheel(self, event):
        self.detail_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _clear_detail(self):
        for w in self.detail_inner.winfo_children():
            w.destroy()

    def _show_welcome(self):
        self._clear_detail()
        self.detail_header.config(text="DEUS 3.0")
        self.detail_subtitle.config(text="Select an agent or pipeline to begin")

        content = tk.Frame(self.detail_inner, bg=BG_PANEL)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        lines = [
            ("DEUS 3.0 — Digital Entity Unification System", ACCENT, FONT_SIZE_LARGE),
            ("", None, None),
            ("Pipeline:", ACCENT, FONT_SIZE),
            ("  Select a pipeline from the sidebar and click to run.", TEXT, FONT_SIZE),
            ("  Or type a command in the console below.", TEXT, FONT_SIZE),
            ("", None, None),
            ("Agents:", ACCENT, FONT_SIZE),
            ("  Click any agent in the sidebar to view its health and details.", TEXT, FONT_SIZE),
            ("", None, None),
            ("Commands:", ACCENT, FONT_SIZE),
            ("  scout / outreach / followup / appointment / closer / report / health", TEXT, FONT_SIZE),
            ("  run <pipeline>   — run a named pipeline", TEXT, FONT_SIZE),
            ("  list             — list pipelines and agents", TEXT, FONT_SIZE),
            ("  help             — show all commands", TEXT, FONT_SIZE),
        ]

        for text, fg, size in lines:
            if text == "":
                tk.Frame(content, bg=BG_PANEL, height=8).pack()
                continue
            tk.Label(content, text=text, bg=BG_PANEL, fg=fg,
                     font=(FONT_FAMILY, size or FONT_SIZE), anchor="w",
                     wraplength=500).pack(fill=tk.X, pady=1)

    def _show_agent_detail(self, agent_name):
        self._clear_detail()
        display = agent_name.replace("_agent", "").replace("_", " ").title()
        self.detail_header.config(text=display)
        self.detail_subtitle.config(text=f"Agent: {agent_name}")

        content = tk.Frame(self.detail_inner, bg=BG_PANEL)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # Health check in thread
        tk.Label(content, text="Health Check:", bg=BG_PANEL, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE, "bold"), anchor="w").pack(fill=tk.X, pady=(0, 4))

        health_frame = tk.Frame(content, bg=BG_CONSOLE, relief=tk.FLAT)
        health_frame.pack(fill=tk.X, pady=(0, 8))

        health_label = tk.Label(health_frame, text="  Checking...", bg=BG_CONSOLE, fg=TEXT_DIM,
                                 font=(FONT_FAMILY, FONT_SIZE), anchor="w", justify=tk.LEFT)
        health_label.pack(fill=tk.X, padx=8, pady=6)

        def do_health():
            try:
                cls = get_agent_class(agent_name)
                if cls is None:
                    health_label.config(text="  Agent class not found.", fg=ERROR)
                    return
                agent = cls()
                h = agent.check_health()
                status_color = SUCCESS if h.healthy else (WARNING if h.status == "degraded" else ERROR)
                health_label.config(
                    text=f"  Status: {h.status.upper()}\n  {h.message}",
                    fg=status_color,
                )
            except Exception as e:
                health_label.config(text=f"  Error: {e}", fg=ERROR)

        threading.Thread(target=do_health, daemon=True).start()

        # Description
        tk.Label(content, text="Description:", bg=BG_PANEL, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE, "bold"), anchor="w").pack(fill=tk.X, pady=(8, 4))

        cls = get_agent_class(agent_name)
        desc = getattr(cls, "description", "No description available.") if cls else "Agent not found."
        tk.Label(content, text=f"  {desc}", bg=BG_PANEL, fg=TEXT,
                 font=(FONT_FAMILY, FONT_SIZE), anchor="w", wraplength=500,
                 justify=tk.LEFT).pack(fill=tk.X)

        # Quick run button
        tk.Label(content, text="", bg=BG_PANEL).pack()
        run_btn = tk.Button(
            content, text=f"Run {display}",
            bg=ACCENT, fg=BG_DARK,
            activebackground=ACCENT_HOVER, activeforeground=BG_DARK,
            font=(FONT_FAMILY, FONT_SIZE, "bold"),
            relief=tk.FLAT, bd=0, cursor="hand2",
            command=lambda: self._run_agent_thread(agent_name),
        )
        run_btn.pack(anchor="w", ipady=4, ipadx=12, pady=(8, 0))

    def _show_result(self, title, message, success=True, agent_name=""):
        self._clear_detail()
        self.detail_header.config(text=title)
        self.detail_subtitle.config(text=agent_name or "Result")

        content = tk.Frame(self.detail_inner, bg=BG_PANEL)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # Status indicator
        status_color = SUCCESS if success else ERROR
        status_text = "SUCCESS" if success else "FAILED"
        tk.Label(content, text=f"  {status_text}", bg=BG_CONSOLE, fg=status_color,
                 font=(FONT_FAMILY, FONT_SIZE_LARGE, "bold"), anchor="w",
                 relief=tk.FLAT).pack(fill=tk.X, pady=(0, 8))

        # Message
        msg_frame = tk.Frame(content, bg=BG_CONSOLE, relief=tk.FLAT)
        msg_frame.pack(fill=tk.BOTH, expand=True)

        msg_text = scrolledtext.ScrolledText(
            msg_frame, bg=BG_CONSOLE, fg=TEXT,
            font=(FONT_FAMILY, FONT_SIZE), relief=tk.FLAT,
            wrap=tk.WORD, state=tk.NORMAL,
        )
        msg_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        msg_text.insert(tk.END, message)
        msg_text.config(state=tk.DISABLED)

    # -- Console --

    def _build_console(self):
        console_frame = tk.Frame(self.root, bg=BG_CONSOLE, height=180)
        console_frame.pack(fill=tk.BOTH, side=tk.BOTTOM)
        console_frame.pack_propagate(False)

        # Header
        hdr = tk.Frame(console_frame, bg=BG_CONSOLE)
        hdr.pack(fill=tk.X, padx=8, pady=(4, 0))

        tk.Label(hdr, text="CONSOLE", bg=BG_CONSOLE, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE, "bold")).pack(side=tk.LEFT)

        tk.Button(hdr, text="Clear", bg=BG_CONSOLE, fg=TEXT_DIM,
                  activebackground=BG_CONSOLE, activeforeground=ACCENT,
                  font=(FONT_FAMILY, 8), relief=tk.FLAT, bd=0, cursor="hand2",
                  command=self._clear_console).pack(side=tk.RIGHT)

        # Log output
        self.console_log = scrolledtext.ScrolledText(
            console_frame, bg=BG_CONSOLE, fg=TEXT,
            font=(FONT_FAMILY, FONT_SIZE), relief=tk.FLAT,
            wrap=tk.WORD, state=tk.DISABLED, height=8,
        )
        self.console_log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 4))

        # Input
        input_frame = tk.Frame(console_frame, bg=BG_CONSOLE)
        input_frame.pack(fill=tk.X, padx=8, pady=(0, 6))

        tk.Label(input_frame, text=">>>", bg=BG_CONSOLE, fg=ACCENT,
                 font=(FONT_FAMILY, FONT_SIZE, "bold")).pack(side=tk.LEFT, padx=(0, 6))

        self.console_input = tk.Entry(
            input_frame, bg=BG_CONSOLE, fg=TEXT_BRIGHT,
            font=(FONT_FAMILY, FONT_SIZE), insertbackground=ACCENT,
            relief=tk.FLAT, bd=0,
        )
        self.console_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.console_input.bind("<Return>", self._on_console_submit)
        self.console_input.bind("<Up>", self._on_history_up)
        self.console_input.bind("<Down>", self._on_history_down)

        self.cmd_history = []
        self.cmd_history_idx = -1

    def _on_console_submit(self, event=None):
        cmd = self.console_input.get().strip()
        if not cmd:
            return
        self.cmd_history.append(cmd)
        self.cmd_history_idx = len(self.cmd_history)
        self.console_input.delete(0, tk.END)

        self._log(f">>> {cmd}", ACCENT)

        # Run command in thread to prevent UI freeze
        threading.Thread(target=self._execute_command, args=(cmd,), daemon=True).start()

    def _on_history_up(self, event=None):
        if self.cmd_history_idx > 0:
            self.cmd_history_idx -= 1
            self.console_input.delete(0, tk.END)
            self.console_input.insert(0, self.cmd_history[self.cmd_history_idx])

    def _on_history_down(self, event=None):
        if self.cmd_history_idx < len(self.cmd_history) - 1:
            self.cmd_history_idx += 1
            self.console_input.delete(0, tk.END)
            self.console_input.insert(0, self.cmd_history[self.cmd_history_idx])
        else:
            self.cmd_history_idx = len(self.cmd_history)
            self.console_input.delete(0, tk.END)

    def _execute_command(self, cmd):
        try:
            result = self.cmd_processor.process(cmd)
            # If it's a quit command, schedule close
            if result.success and result.message == "quit":
                self.root.after(0, self.root.destroy)
                return
            # Show result in detail panel
            self.root.after(0, lambda: self._show_result(
                f"Command: {cmd}",
                result.message,
                success=result.success,
                agent_name=result.agent_name or result.pipeline_name,
            ))
            self._log(result.message, SUCCESS if result.success else ERROR)
        except Exception as e:
            self._log(f"Error: {e}", ERROR)

    def _log(self, text, fg=TEXT):
        """Thread-safe log to console."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_queue.put((f"[{timestamp}] {text}", fg))

    def _poll_log_queue(self):
        """Drain the log queue into the console widget."""
        while not self.log_queue.empty():
            text, fg = self.log_queue.get_nowait()
            self.console_log.config(state=tk.NORMAL)
            self.console_log.insert(tk.END, text + "\n", fg)
            self.console_log.see(tk.END)
            self.console_log.config(state=tk.DISABLED)
        self.root.after(100, self._poll_log_queue)

    def _clear_console(self):
        self.console_log.config(state=tk.NORMAL)
        self.console_log.delete("1.0", tk.END)
        self.console_log.config(state=tk.DISABLED)

    # -- Threading helpers --

    def _run_pipeline_thread(self, name):
        if self.running:
            self._log("Already running a pipeline. Wait for it to finish.", WARNING)
            return
        self.running = True
        self._log(f"Starting pipeline: {name}", ACCENT)
        self.root.after(0, lambda: self._show_result(
            f"Pipeline: {name}", "Running...", success=True, agent_name="pipeline"
        ))
        threading.Thread(target=self._run_pipeline_worker, args=(name,), daemon=True).start()

    def _run_pipeline_worker(self, name):
        try:
            pipeline = Pipeline(pipeline_name=name)
            result = pipeline.run()
            self._log(result.message, SUCCESS if result.success else ERROR)
            self.root.after(0, lambda: self._show_result(
                f"Pipeline: {name}",
                result.message,
                success=result.success,
                agent_name=f"{len(result.steps)} steps | {result.total_duration:.1f}s",
            ))
        except Exception as e:
            self._log(f"Pipeline error: {e}", ERROR)
        finally:
            self.running = False

    def _run_agent_thread(self, agent_name):
        if self.running:
            self._log("Already running. Wait for it to finish.", WARNING)
            return
        self.running = True
        self._log(f"Running agent: {agent_name}", ACCENT)
        threading.Thread(target=self._run_agent_worker, args=(agent_name,), daemon=True).start()

    def _run_agent_worker(self, agent_name):
        try:
            cls = get_agent_class(agent_name)
            if cls is None:
                self._log(f"Agent class not found: {agent_name}", ERROR)
                return
            agent = cls()
            result = agent.run()
            self._log(result.message, SUCCESS if result.success else ERROR)
            self.root.after(0, lambda: self._show_result(
                agent_name.replace("_agent", "").replace("_", " ").title(),
                result.message,
                success=result.success,
                agent_name=agent_name,
            ))
        except Exception as e:
            self._log(f"Agent error: {e}", ERROR)
        finally:
            self.running = False

    # -----------------------------------------------------------------------
    # Run
    # -----------------------------------------------------------------------

    def run(self):
        self._log("DEUS 3.0 GUI started.", ACCENT)
        self._log("Type 'help' in the console for available commands.", TEXT_DIM)
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = DeusGUI()
    app.run()
