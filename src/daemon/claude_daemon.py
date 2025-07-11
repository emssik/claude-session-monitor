#!/usr/bin/env python3
"""
Core daemon implementation for Claude session monitor.
Provides background monitoring service that continuously tracks Claude API usage.
"""
import threading
import time
import signal
import logging
from typing import Optional, Callable
from datetime import datetime, timezone

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared.data_models import ConfigData, MonitoringData
from shared.constants import DEFAULT_CCUSAGE_FETCH_INTERVAL_SECONDS
from shared.file_manager import DataFileManager
from .data_collector import DataCollector
from .notification_manager import NotificationManager
from .session_activity_tracker import SessionActivityTracker
from .subprocess_pool import get_subprocess_pool


class ClaudeDaemon:
    """
    Core daemon class for background Claude API monitoring.
    
    Provides:
    - Daemon lifecycle management (start/stop)
    - Signal handling for graceful shutdown
    - Threaded monitoring loop with configurable intervals
    - Integration with shared infrastructure
    """
    
    def __init__(self, config: ConfigData):
        """
        Initialize the daemon with configuration.
        
        Args:
            config: Configuration data containing monitoring settings
        """
        self.config = config
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        
        # Set up logging
        self._setup_logging()
        
        # Register signal handlers
        self._setup_signal_handlers()
        
        # Data collection component
        self.data_collector = DataCollector(config)
        
        # File management component
        self.file_manager = DataFileManager()
        
        # Notification management component
        self.notification_manager = NotificationManager()
        
        # Session activity tracking component
        self.session_activity_tracker = SessionActivityTracker()
        
        # Set up symlinks for compatibility with spec
        self._setup_symlinks()
        
        self.logger.info(f"Daemon initialized with fetch interval: {config.ccusage_fetch_interval_seconds}s")
    
    def _setup_symlinks(self):
        """Set up symlinks for compatibility with spec."""
        try:
            from shared.utils import get_project_cache_file_path
            from shared.constants import HOOK_LOG_DIR
            
            # Source: real cache file in config directory
            source_path = get_project_cache_file_path()
            
            # Target: symlink in /tmp/claude-monitor/
            tmp_dir = HOOK_LOG_DIR
            target_path = os.path.join(tmp_dir, "project_cache.json")
            
            # Ensure /tmp/claude-monitor/ exists
            os.makedirs(tmp_dir, exist_ok=True)
            
            # Remove existing symlink if it exists
            if os.path.islink(target_path):
                os.unlink(target_path)
            elif os.path.exists(target_path):
                # If it's a regular file, remove it
                os.remove(target_path)
            
            # Create symlink
            os.symlink(source_path, target_path)
            self.logger.info(f"Created symlink: {target_path} -> {source_path}")
            
        except Exception as e:
            self.logger.warning(f"Failed to create project cache symlink: {e}")
    
    def _setup_logging(self):
        """Set up logging for the daemon."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, shutting down gracefully...")
            self.stop()
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    def start(self):
        """
        Start the daemon in a background thread.
        
        This method is idempotent - calling it multiple times has no effect
        if the daemon is already running.
        """
        with self._lock:
            if self.is_running:
                self.logger.warning("Daemon is already running")
                return
            
            self.logger.info("Starting daemon...")
            self.is_running = True
            self._stop_event.clear()
            
            # Start the monitoring thread
            self._thread = threading.Thread(target=self._main_loop, daemon=True)
            self._thread.start()
            
            self.logger.info("Daemon started successfully")
    
    def stop(self):
        """
        Stop the daemon and wait for the thread to finish.
        
        This method is idempotent - calling it multiple times is safe.
        """
        with self._lock:
            if not self.is_running:
                return
            
            self.logger.info("Stopping daemon...")
            self.is_running = False
            self._stop_event.set()
        
        # Wait for the thread to finish (outside the lock)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                self.logger.warning("Daemon thread did not stop within timeout")
            else:
                self.logger.info("Daemon stopped successfully")
                
        # Shutdown subprocess pool
        try:
            pool = get_subprocess_pool()
            pool.stop()
            self.logger.info("Subprocess pool shut down successfully")
        except Exception as e:
            self.logger.error(f"Error shutting down subprocess pool: {e}")
    
    def _main_loop(self):
        """
        Main monitoring loop that runs in the background thread.
        
        Continuously monitors Claude API usage at configured intervals,
        with proper error handling to ensure daemon stability.
        """
        self.logger.info("Daemon main loop started")
        
        last_collection_time = 0
        collection_interval = self.config.ccusage_fetch_interval_seconds
        
        while not self._stop_event.is_set():
            try:
                current_time = time.time()
                
                # Check if it's time to collect data
                if current_time - last_collection_time >= collection_interval:
                    self._collect_data()
                    last_collection_time = current_time
                
                # Sleep for a short interval to prevent busy waiting
                time.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"Error in daemon main loop: {e}")
                # Continue running despite errors
                time.sleep(1)
        
        self.logger.info("Daemon main loop stopped")
    
    def _collect_data(self):
        """
        Collect monitoring data using DataCollector.
        """
        try:
            self.logger.debug("Collecting monitoring data...")
            monitoring_data = self.data_collector.collect_data()
            
            # Log summary of collected data
            sessions_count = len(monitoring_data.current_sessions)
            total_cost = monitoring_data.total_cost_this_month
            self.logger.info(f"Collected {sessions_count} sessions, total cost: ${total_cost:.4f}")
            
            # Save data to file using FileManager with error handling
            try:
                success = self.file_manager.write_monitoring_data(monitoring_data.to_dict())
                if success:
                    self.logger.debug("Data saved to file successfully")
                else:
                    self.logger.warning("Failed to save data to file")
            except Exception as e:
                self.logger.error(f"Error saving data to file: {e}")
                # Continue running despite file write errors
            
            # Clean up old activity sessions (5h billing window)
            try:
                self.session_activity_tracker.cleanup_completed_billing_sessions()
                self.logger.debug("Activity session cleanup completed")
            except Exception as e:
                self.logger.error(f"Error during activity session cleanup: {e}")
                # Continue running despite cleanup errors
            
            # Check for notification conditions
            self._check_notification_conditions(monitoring_data)
            
        except RuntimeError as e:
            # Log collection failures but don't stop the daemon
            error_status = self.data_collector.get_error_status()
            if error_status and error_status.consecutive_failures > 5:
                self.logger.warning(f"Data collection has failed {error_status.consecutive_failures} consecutive times")
                # Send error notification for repeated failures
                self._send_error_notification(error_status)
            else:
                self.logger.error(f"Data collection failed: {e}")
    
    def _check_notification_conditions(self, monitoring_data: MonitoringData):
        """
        Check monitoring data for notification conditions and send alerts as needed.
        
        Args:
            monitoring_data: Current monitoring data
        """
        try:
            # Check each active session for time warnings, inactivity, and max tokens
            for session in monitoring_data.current_sessions:
                if not session.is_active or session.end_time is None:
                    continue
                
                # Check for real-time max tokens update (like old system)
                if self.data_collector.update_max_tokens_if_higher(session.total_tokens):
                    self.logger.info(f"New maximum tokens found during active session: {session.total_tokens:,}")
                
                # Check time remaining warning
                time_remaining = session.end_time - datetime.now(timezone.utc)
                minutes_remaining = int(time_remaining.total_seconds() / 60)
                
                if 0 < minutes_remaining <= self.config.time_remaining_alert_minutes:
                    self.notification_manager.send_time_warning(minutes_remaining)
                
                # Check inactivity (simplified - using start_time as proxy for last activity)
                time_since_start = datetime.now(timezone.utc) - session.start_time
                minutes_since_start = int(time_since_start.total_seconds() / 60)
                
                # If session is long-running (over 1 hour), consider it potentially inactive
                if minutes_since_start >= 60 and minutes_since_start % self.config.inactivity_alert_minutes == 0:
                    # Send inactivity alert every inactivity_alert_minutes for long sessions
                    if minutes_since_start >= self.config.inactivity_alert_minutes * 6:  # After 1 hour minimum
                        minutes_inactive = minutes_since_start - 60  # Approximate inactivity
                        self.notification_manager.send_inactivity_alert(minutes_inactive)
        
        except Exception as e:
            self.logger.error(f"Error checking notification conditions: {e}")
    
    def _send_error_notification(self, error_status):
        """
        Send error notification for repeated failures.
        
        Args:
            error_status: ErrorStatus object with failure information
        """
        try:
            error_message = f"{error_status.consecutive_failures} consecutive failures: {error_status.error_message}"
            self.notification_manager.send_error_notification(error_message)
        except Exception as e:
            self.logger.error(f"Failed to send error notification: {e}")
    
    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()