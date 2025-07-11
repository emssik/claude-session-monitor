#!/usr/bin/env python3
"""
Tests for Claude daemon core functionality.
Tests daemon lifecycle, signal handling, and integration with shared infrastructure.
"""
import unittest
import threading
import time
import signal
import tempfile
import os
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from daemon.claude_daemon import ClaudeDaemon
from shared.data_models import MonitoringData, ConfigData, SessionData
from shared.constants import DEFAULT_CCUSAGE_FETCH_INTERVAL_SECONDS


class TestClaudeDaemon(unittest.TestCase):
    """Test cases for ClaudeDaemon class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.data_path = os.path.join(self.temp_dir, "data.json")
        
        # Create test config
        self.test_config = ConfigData(
            refresh_interval_seconds=1,
            ccusage_fetch_interval_seconds=2
        )

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_daemon_initialization(self):
        """Test basic daemon initialization."""
        daemon = ClaudeDaemon(self.test_config)
        
        self.assertIsNotNone(daemon)
        self.assertEqual(daemon.config, self.test_config)
        self.assertFalse(daemon.is_running)
        self.assertIsNone(daemon._thread)

    def test_daemon_start_stop_lifecycle(self):
        """Test daemon start and stop lifecycle."""
        daemon = ClaudeDaemon(self.test_config)
        
        # Test start
        daemon.start()
        self.assertTrue(daemon.is_running)
        self.assertIsNotNone(daemon._thread)
        self.assertTrue(daemon._thread.is_alive())
        
        # Let it run briefly
        time.sleep(0.1)
        
        # Test stop
        daemon.stop()
        self.assertFalse(daemon.is_running)
        
        # Wait for thread to finish
        if daemon._thread and daemon._thread.is_alive():
            daemon._thread.join(timeout=2)
        
        self.assertFalse(daemon._thread.is_alive() if daemon._thread else True)

    def test_daemon_signal_handling(self):
        """Test daemon graceful shutdown on signals."""
        # Mock signal handlers during initialization
        with patch('signal.signal') as mock_signal:
            daemon = ClaudeDaemon(self.test_config)
            
            # Verify signal handlers were set during initialization
            self.assertEqual(mock_signal.call_count, 2)
            calls = mock_signal.call_args_list
            
            # Check SIGTERM and SIGINT handlers
            signals_handled = [call[0][0] for call in calls]
            self.assertIn(signal.SIGTERM, signals_handled)
            self.assertIn(signal.SIGINT, signals_handled)
            
            daemon.start()
            daemon.stop()

    def test_daemon_main_loop_timing(self):
        """Test that daemon respects timing intervals."""
        # Use short intervals for testing
        test_config = ConfigData(
            refresh_interval_seconds=0.1,
            ccusage_fetch_interval_seconds=0.2
        )
        
        daemon = ClaudeDaemon(test_config)
        
        # Mock the data collection to track calls
        daemon._collect_data = Mock()
        
        daemon.start()
        
        # Let it run for a short time
        time.sleep(0.3)
        
        daemon.stop()
        
        # Verify data collection was called
        self.assertGreater(daemon._collect_data.call_count, 0)

    def test_daemon_double_start_prevention(self):
        """Test that daemon prevents double start."""
        daemon = ClaudeDaemon(self.test_config)
        
        daemon.start()
        
        # Try to start again - should not create new thread
        original_thread = daemon._thread
        daemon.start()
        
        self.assertEqual(daemon._thread, original_thread)
        
        daemon.stop()

    def test_daemon_stop_idempotent(self):
        """Test that daemon stop is idempotent."""
        daemon = ClaudeDaemon(self.test_config)
        
        # Stop without starting should not error
        daemon.stop()
        self.assertFalse(daemon.is_running)
        
        # Start and stop multiple times
        daemon.start()
        daemon.stop()
        daemon.stop()  # Second stop should be safe
        
        self.assertFalse(daemon.is_running)

    def test_daemon_context_manager(self):
        """Test daemon as context manager."""
        with ClaudeDaemon(self.test_config) as daemon:
            self.assertTrue(daemon.is_running)
            time.sleep(0.1)
        
        self.assertFalse(daemon.is_running)

    def test_daemon_error_handling_in_main_loop(self):
        """Test daemon continues running despite errors in main loop."""
        daemon = ClaudeDaemon(self.test_config)
        
        # Mock data collection to raise an error
        daemon._collect_data = Mock(side_effect=Exception("Test error"))
        
        daemon.start()
        
        # Let it run despite errors
        time.sleep(0.2)
        
        # Daemon should still be running
        self.assertTrue(daemon.is_running)
        
        daemon.stop()

    def test_daemon_thread_safety(self):
        """Test daemon thread safety."""
        daemon = ClaudeDaemon(self.test_config)
        
        def start_stop_daemon():
            daemon.start()
            time.sleep(0.1)
            daemon.stop()
        
        # Run multiple threads trying to start/stop
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=start_stop_daemon)
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Daemon should be stopped and not crashed
        self.assertFalse(daemon.is_running)

    def test_daemon_saves_data_to_file(self):
        """Test daemon integration with file manager - saves data to disk."""
        from shared.file_manager import DataFileManager
        
        daemon = ClaudeDaemon(self.test_config)
        
        # Mock data collector to return test data
        test_session = SessionData(
            session_id="test-session",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            input_tokens=1000,
            output_tokens=2000,
            total_tokens=3000,
            cost_usd=0.50,
            is_active=True
        )
        
        test_monitoring_data = MonitoringData(
            current_sessions=[test_session],
            total_sessions_this_month=1,
            total_cost_this_month=0.50,
            max_tokens_per_session=3000,
            last_update=datetime.now(timezone.utc),
            billing_period_start=datetime.now(timezone.utc) - timedelta(days=1),
            billing_period_end=datetime.now(timezone.utc) + timedelta(days=29)
        )
        
        # Mock the daemon's data collector and file manager directly
        daemon.data_collector = Mock()
        daemon.data_collector.collect_data.return_value = test_monitoring_data
        
        daemon.file_manager = Mock()
        
        daemon.start()
        
        # Let daemon run for a brief time
        time.sleep(0.3)
        
        daemon.stop()
        
        # Verify data collector was called
        self.assertGreater(daemon.data_collector.collect_data.call_count, 0)
        
        # Verify file manager was called to save data
        self.assertGreater(daemon.file_manager.write_monitoring_data.call_count, 0)
        
        # Verify correct data was saved
        saved_data = daemon.file_manager.write_monitoring_data.call_args_list[0][0][0]
        self.assertEqual(saved_data['total_sessions_this_month'], 1)
        self.assertEqual(saved_data['total_cost_this_month'], 0.50)
        self.assertEqual(saved_data['max_tokens_per_session'], 3000)

    def test_daemon_calls_session_cleanup_integration(self):
        """Test that daemon automatically calls session activity cleanup."""
        test_config = ConfigData(
            refresh_interval_seconds=0.1,
            ccusage_fetch_interval_seconds=0.2  # Fast interval for testing
        )
        
        daemon = ClaudeDaemon(test_config)
        
        # Mock components to avoid side effects but allow _collect_data to run
        daemon.data_collector.collect_data = Mock(return_value=MonitoringData(
            current_sessions=[],
            total_sessions_this_month=0,
            total_cost_this_month=0.0,
            max_tokens_per_session=10000,
            last_update=datetime.now(timezone.utc),
            billing_period_start=datetime.now(timezone.utc),
            billing_period_end=datetime.now(timezone.utc) + timedelta(days=30)
        ))
        daemon.file_manager.write_monitoring_data = Mock(return_value=True)
        daemon._check_notification_conditions = Mock()
        
        # Mock the session activity tracker cleanup method to track calls
        daemon.session_activity_tracker.cleanup_completed_billing_sessions = Mock()
        
        daemon.start()
        
        # Let it run for sufficient time to trigger cleanup calls
        time.sleep(0.5)
        
        daemon.stop()
        
        # Verify that cleanup_completed_billing_sessions was called
        self.assertGreater(
            daemon.session_activity_tracker.cleanup_completed_billing_sessions.call_count, 
            0,
            "Session cleanup should have been called by daemon"
        )

    def test_daemon_initializes_session_activity_tracker(self):
        """Test that daemon properly initializes SessionActivityTracker."""
        daemon = ClaudeDaemon(self.test_config)
        
        # Verify that session activity tracker is initialized
        self.assertTrue(hasattr(daemon, 'session_activity_tracker'))
        self.assertIsNotNone(daemon.session_activity_tracker)


if __name__ == '__main__':
    unittest.main()