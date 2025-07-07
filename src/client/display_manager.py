#!/usr/bin/env python3

import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

try:
    from ..shared.data_models import MonitoringData, SessionData, ActivitySessionData
except ImportError:
    from shared.data_models import MonitoringData, SessionData, ActivitySessionData


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


class DisplayManager:
    """
    Manages terminal display output for the Claude monitor client.
    
    Provides the same UI/layout as the original claude_monitor.py
    with progress bars, colors, and formatting.
    """

    def __init__(self, total_monthly_sessions: int = 50):
        """
        Initialize DisplayManager.
        
        Args:
            total_monthly_sessions: Expected monthly session limit for calculations
        """
        self.total_monthly_sessions = total_monthly_sessions
        self._screen_cleared = False
        
        # Activity session display configuration
        self.activity_config = {
            "enabled": True,
            "show_inactive_sessions": True,
            "max_sessions_displayed": 10,
            "status_icons": {
                "ACTIVE": "🔵",
                "WAITING_FOR_USER": "⏳",
                "IDLE": "💤", 
                "INACTIVE": "⚫",
                "STOPPED": "⛔"
            },
            "status_colors": {
                "ACTIVE": Colors.GREEN,
                "WAITING_FOR_USER": Colors.WARNING,
                "IDLE": Colors.CYAN,
                "INACTIVE": Colors.FAIL,
                "STOPPED": Colors.FAIL
            },
            "max_session_id_length": 12,
            "show_timestamps": True,
            "verbosity": "normal"  # "minimal", "normal", "verbose"
        }

    def create_progress_bar(self, percentage: float, width: int = 40) -> str:
        """
        Create a progress bar string identical to claude_monitor.py.
        
        Args:
            percentage: Progress percentage (0-100)
            width: Width of progress bar in characters
            
        Returns:
            Formatted progress bar string
        """
        filled_width = int(width * percentage / 100)
        bar = '█' * filled_width + ' ' * (width - filled_width)
        return f"[{bar}]"

    def format_timedelta(self, td: timedelta) -> str:
        """
        Format timedelta as "Xh YYm" identical to claude_monitor.py.
        
        Args:
            td: Time delta to format
            
        Returns:
            Formatted time string
        """
        total_seconds = int(td.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours}h {minutes:02d}m"

    def clear_screen(self):
        """Clear screen and hide cursor like claude_monitor.py."""
        print("\033[H\033[J\033[?25l", end="")
    
    def move_to_top(self):
        """Move cursor to top without clearing screen - prevents flicker."""
        print("\033[H", end="")

    def calculate_token_usage_percentage(self, current_tokens: int, max_tokens: int) -> float:
        """
        Calculate token usage percentage.
        
        Args:
            current_tokens: Current token count
            max_tokens: Maximum token limit
            
        Returns:
            Percentage as float
        """
        if max_tokens <= 0:
            return 0.0
        return (current_tokens / max_tokens) * 100

    def calculate_time_progress_percentage(self, start_time: datetime, end_time: datetime, 
                                         current_time: datetime) -> float:
        """
        Calculate time progress percentage through session.
        
        Args:
            start_time: Session start time
            end_time: Session end time  
            current_time: Current time
            
        Returns:
            Progress percentage as float
        """
        time_remaining = end_time - current_time
        time_total = end_time - start_time
        
        if time_total.total_seconds() <= 0:
            return 100.0
            
        progress = (1 - (time_remaining.total_seconds() / time_total.total_seconds())) * 100
        return max(0.0, min(100.0, progress))

    def find_active_session(self, monitoring_data: MonitoringData) -> Optional[SessionData]:
        """
        Find the currently active session from monitoring data.
        
        Args:
            monitoring_data: Current monitoring data
            
        Returns:
            Active session if found, None otherwise
        """
        for session in monitoring_data.current_sessions:
            if session.is_active:
                return session
        return None

    def calculate_session_stats(self, total_monthly_sessions: int, current_sessions: int,
                              days_in_period: int, days_remaining: int) -> Dict[str, Any]:
        """
        Calculate session usage statistics.
        
        Args:
            total_monthly_sessions: Total sessions allowed per month
            current_sessions: Sessions used so far
            days_in_period: Total days in billing period
            days_remaining: Days remaining in period
            
        Returns:
            Dictionary with session statistics
        """
        sessions_used = current_sessions
        sessions_left = total_monthly_sessions - current_sessions
        
        # Calculate average sessions per day for remaining period
        if days_remaining > 0:
            avg_sessions_per_day = sessions_left / days_remaining
        else:
            avg_sessions_per_day = float(sessions_left)  # If last day
            
        return {
            'sessions_used': sessions_used,
            'sessions_left': sessions_left,
            'avg_sessions_per_day': avg_sessions_per_day
        }

    def render_active_session_display(self, monitoring_data: MonitoringData, 
                                    active_session: SessionData):
        """
        Render display for when there's an active session.
        
        Args:
            monitoring_data: Current monitoring data
            active_session: The active session to display
        """
        # Calculate token usage percentage
        token_usage_percent = self.calculate_token_usage_percentage(
            active_session.total_tokens, monitoring_data.max_tokens_per_session
        )
        
        # Calculate time progress
        current_time = datetime.now(timezone.utc)
        time_progress_percent = self.calculate_time_progress_percentage(
            active_session.start_time, active_session.end_time, current_time
        )
        
        # Calculate time remaining
        time_remaining = active_session.end_time - current_time
        
        # Display progress bars (same format as claude_monitor.py)
        print(f"Token Usage:   {Colors.GREEN}{self.create_progress_bar(token_usage_percent)}{Colors.ENDC} {token_usage_percent:.1f}%")
        print(f"Time to Reset: {Colors.BLUE}{self.create_progress_bar(time_progress_percent)}{Colors.ENDC} {self.format_timedelta(time_remaining)}")
        
        # Display session details
        print(f"\n{Colors.BOLD}Tokens:{Colors.ENDC}        {active_session.total_tokens:,} / ~{monitoring_data.max_tokens_per_session:,}")
        print(f"{Colors.BOLD}Session Cost:{Colors.ENDC}  ${active_session.cost_usd:.2f}\n")

    def render_waiting_display(self, monitoring_data: MonitoringData):
        """
        Render display when waiting for a new session to start.
        
        Args:
            monitoring_data: Current monitoring data
        """
        print(f"\n{Colors.WARNING}Waiting for a new session to start...{Colors.ENDC}\n")
        print(f"Saved max tokens: {monitoring_data.max_tokens_per_session:,}")
        
        # Show current subscription period start
        period_start = monitoring_data.billing_period_start.strftime('%Y-%m-%d')
        print(f"Current subscription period started: {period_start}\n")

    def render_footer(self, current_time: datetime, session_stats: Dict[str, Any],
                     days_remaining: int, total_cost: float, daemon_version: Optional[str] = None):
        """
        Render footer with session statistics and cost.
        
        Args:
            current_time: Current local time
            session_stats: Session usage statistics
            days_remaining: Days remaining in billing period
            total_cost: Total cost for the month
            daemon_version: Daemon version if available
        """
        print("=" * 60)
        
        # Footer line 1: Time, sessions, cost
        footer_line1 = (
            f"⏰ {current_time.strftime('%H:%M:%S')}   "
            f"🗓️ Sessions: {Colors.BOLD}{session_stats['sessions_used']} used, "
            f"{session_stats['sessions_left']} left{Colors.ENDC} | "
            f"💰 Cost (mo): ${total_cost:.2f}"
        )
        
        # Footer line 2: Shortened for better readability
        version_info = daemon_version if daemon_version else "unknown"
        footer_line2 = (
            f"  └─ ⏳ {days_remaining}d left "
            f"(avg {session_stats['avg_sessions_per_day']:.1f}/day) | "
            f"🖥️ Server: {version_info} | Ctrl+C exit"
        )
        
        print(footer_line1)
        print(footer_line2)

    def _render_activity_sessions(self, activity_sessions: List[ActivitySessionData]):
        """
        Render Claude Code activity sessions with configurable display options.
        
        Args:
            activity_sessions: List of activity sessions to display
        """
        # Check if activity sessions display is enabled
        if not self.activity_config["enabled"]:
            return
        
        if not activity_sessions:
            print(f"\n{Colors.CYAN}No activity sessions found{Colors.ENDC}")
            return
        
        # Filter sessions based on configuration
        filtered_sessions = self._filter_activity_sessions(activity_sessions)
        
        if not filtered_sessions:
            if self.activity_config["verbosity"] != "minimal":
                print(f"\n{Colors.CYAN}No activity sessions to display{Colors.ENDC}")
            return
        
        # Activity sessions header
        verbosity = self.activity_config["verbosity"]
        if verbosity == "minimal":
            print(f"\n{Colors.HEADER}Activity: {len(filtered_sessions)} sessions{Colors.ENDC}")
        else:
            print(f"\n{Colors.HEADER}{Colors.BOLD}CLAUDE CODE ACTIVITY{Colors.ENDC}")
            print(f"{Colors.HEADER}{'=' * 20}{Colors.ENDC}")
        
        # Display sessions based on verbosity
        for session in filtered_sessions:
            self._render_single_activity_session(session, verbosity)
        
        if verbosity != "minimal":
            print()  # Empty line after activity sessions

    def _filter_activity_sessions(self, sessions: List[ActivitySessionData]) -> List[ActivitySessionData]:
        """
        Filter activity sessions based on configuration.
        
        Args:
            sessions: List of all activity sessions
            
        Returns:
            Filtered list of sessions
        """
        filtered = sessions
        
        # Filter out inactive sessions if configured
        if not self.activity_config["show_inactive_sessions"]:
            filtered = [s for s in filtered if s.status != "INACTIVE"]
        
        # Sort by start time (most recent first) and limit
        filtered = sorted(filtered, key=lambda s: s.start_time, reverse=True)
        max_sessions = self.activity_config["max_sessions_displayed"]
        
        return filtered[:max_sessions]

    def _render_single_activity_session(self, session: ActivitySessionData, verbosity: str):
        """
        Render a single activity session based on verbosity level.
        
        Args:
            session: Activity session to render
            verbosity: Display verbosity level
        """
        # Get icon and color from configuration
        icon = self.activity_config["status_icons"].get(session.status, "❓")
        color = self.activity_config["status_colors"].get(session.status, Colors.ENDC)
        
        # Format session ID with truncation
        max_length = self.activity_config["max_session_id_length"]
        session_id_display = session.session_id[:max_length] + "..." if len(session.session_id) > max_length else session.session_id
        
        if verbosity == "minimal":
            # Compact display: just icon and status
            print(f"{icon} {color}{session.status}{Colors.ENDC}", end=" ")
        elif verbosity == "normal":
            # Normal display: icon, session ID, status, optional timestamp
            time_str = ""
            if self.activity_config["show_timestamps"]:
                time_str = f" ({session.start_time.strftime('%H:%M:%S')})"
            
            print(f"{icon} {color}{Colors.BOLD}{session_id_display}{Colors.ENDC} - {color}{session.status}{Colors.ENDC}{time_str}")
        elif verbosity == "verbose":
            # Verbose display: all details including event type and metadata
            time_str = session.start_time.strftime('%Y-%m-%d %H:%M:%S')
            event_info = f" [{session.event_type}]" if session.event_type else ""
            
            print(f"{icon} {color}{Colors.BOLD}{session_id_display}{Colors.ENDC}")
            print(f"   Status: {color}{session.status}{Colors.ENDC} | Time: {time_str}{event_info}")
            
            if session.metadata:
                metadata_str = ", ".join([f"{k}={v}" for k, v in session.metadata.items()])
                print(f"   Metadata: {metadata_str}")
        
        # Add newline for minimal mode after all sessions
        if verbosity == "minimal":
            print()  # Single newline at the end

    def render_full_display(self, monitoring_data: MonitoringData):
        """
        Render the complete display exactly like claude_monitor.py.
        
        Args:
            monitoring_data: Current monitoring data to display
        """
        # Clear screen only on first run, then just move to top
        if not self._screen_cleared:
            self.clear_screen()
            self._screen_cleared = True
        else:
            self.move_to_top()
        
        # Header (same as claude_monitor.py)
        print(f"{Colors.HEADER}{Colors.BOLD}✦ ✧ ✦ CLAUDE SESSION MONITOR ✦ ✧ ✦{Colors.ENDC}")
        print(f"{Colors.HEADER}{'=' * 35}{Colors.ENDC}\n")
        
        # Get current time
        current_time = datetime.now()
        
        # Calculate billing period info
        period_duration = monitoring_data.billing_period_end - monitoring_data.billing_period_start
        days_in_period = period_duration.days
        days_remaining = (monitoring_data.billing_period_end.date() - datetime.now(timezone.utc).date()).days
        
        # Calculate session statistics
        session_stats = self.calculate_session_stats(
            self.total_monthly_sessions,
            monitoring_data.total_sessions_this_month,
            days_in_period,
            days_remaining
        )
        
        # Check for active session
        active_session = self.find_active_session(monitoring_data)
        
        if active_session:
            # Render active session display
            self.render_active_session_display(monitoring_data, active_session)
        else:
            # Render waiting display
            self.render_waiting_display(monitoring_data)
        
        # Render activity sessions if available
        activity_sessions = getattr(monitoring_data, 'activity_sessions', None) or []
        self._render_activity_sessions(activity_sessions)
        
        # Render footer
        self.render_footer(current_time, session_stats, days_remaining, 
                          monitoring_data.total_cost_this_month, monitoring_data.daemon_version)
        
        # Flush output
        sys.stdout.flush()

    def show_cursor(self):
        """Show terminal cursor."""
        print("\033[?25h", end="")

    def show_exit_message(self):
        """Show exit message when closing monitor."""
        self.show_cursor()
        print(f"\n\n{Colors.WARNING}Closing monitor...{Colors.ENDC}")

    def show_error_message(self, message: str):
        """
        Show error message in red.
        
        Args:
            message: Error message to display
        """
        print(f"{Colors.FAIL}Error: {message}{Colors.ENDC}")

    def show_warning_message(self, message: str):
        """
        Show warning message in yellow.
        
        Args:
            message: Warning message to display
        """
        print(f"{Colors.WARNING}Warning: {message}{Colors.ENDC}")

    def show_info_message(self, message: str):
        """
        Show info message in cyan.
        
        Args:
            message: Info message to display
        """
        print(f"{Colors.CYAN}{message}{Colors.ENDC}")
    
    def render_daemon_offline_display(self):
        """
        Render full-screen display when daemon is offline, matching claude_monitor.py style.
        """
        # Clear screen only on first run, then just move to top
        if not self._screen_cleared:
            self.clear_screen()
            self._screen_cleared = True
        else:
            self.move_to_top()
        
        # Header (same as normal display)
        print(f"{Colors.HEADER}{Colors.BOLD}✦ ✧ ✦ CLAUDE SESSION MONITOR ✦ ✧ ✦{Colors.ENDC}")
        print(f"{Colors.HEADER}{'=' * 35}{Colors.ENDC}\n")
        
        # Server status message
        print(f"\n{Colors.FAIL}⚠️  SERVER NOT RUNNING{Colors.ENDC}")
        print(f"\n{Colors.WARNING}The Claude monitor server is currently offline.{Colors.ENDC}")
        print(f"{Colors.WARNING}Please start the server to see real-time monitoring data.{Colors.ENDC}\n")
        
        # Instructions
        print(f"{Colors.CYAN}To start the server:{Colors.ENDC}")
        print(f"  python3 -m src.daemon.claude_daemon\n")
        print(f"{Colors.CYAN}Or use the original monitor:{Colors.ENDC}")
        print(f"  python3 claude_monitor.py\n")
        
        # Footer (simplified)
        current_time = datetime.now()
        print("=" * 60)
        print(f"⏰ {current_time.strftime('%H:%M:%S')}   🖥️ Server: {Colors.FAIL}OFFLINE{Colors.ENDC} | Ctrl+C exit")
        
        # Flush output
        sys.stdout.flush()