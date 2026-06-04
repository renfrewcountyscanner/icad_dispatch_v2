#!/usr/bin/env python3
#

"""
Author: Ian Carey
Date: 2020-23-04
Description: A custom logging module with thread support, colored console output, and log rotation.

Usage: In main script import and start CustomLogger
`logger = CustomLogger(log_level, logger_name, log_path, show_threads=True, backup_count=7, enable_file_logs=True).logger`

Log Levels:
1: Debug | logger.debug()
2: Info | logger.info()
3: Warning | logger.warning()
4: Error | logger.error()
5: Critical | log.critical()

Requirements:
- Python 3.12+
- colorama~=0.4.6

"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from colorama import Fore, Style


class ColoredFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, show_threads=True):
        super().__init__(fmt, datefmt)
        self.show_threads = show_threads
        self.COLOR_CONFIG = {
            logging.DEBUG: (Fore.CYAN, f'[{Style.BRIGHT}{Fore.CYAN}^{Style.RESET_ALL}]'),
            logging.INFO: (Fore.GREEN, f'[{Style.BRIGHT}{Fore.GREEN}+{Style.RESET_ALL}]'),
            logging.WARNING: (Fore.YELLOW, f'[{Style.BRIGHT}{Fore.YELLOW}!{Style.RESET_ALL}]'),
            logging.ERROR: (Fore.RED, f'[{Style.BRIGHT}{Fore.RED}#{Style.RESET_ALL}]'),
            logging.CRITICAL: (Fore.MAGENTA, f'[{Style.BRIGHT}{Fore.MAGENTA}*{Style.RESET_ALL}]'),
        }

    def format(self, record):
        color, icon = self.COLOR_CONFIG.get(record.levelno, ('', ''))
        reset = Style.RESET_ALL

        super().format(record)

        for word in record.message.split():
            if word.startswith('<<') and word.endswith('>>'):
                record.message = record.message.replace(word, f'{color}{word[2:-2]}{reset}')

        if self.show_threads:
            thread_info = f" [{record.threadName}]"
        else:
            thread_info = ""

        return f'{record.asctime}{thread_info} {color}{record.levelname}:{reset} {icon} {record.message}'


class CustomLogger:
    def __init__(self, log_level, logger_name, log_path, show_threads=True, backup_count=7, enable_file_logs=True):
        """
        Initialize a custom logger with optional thread and log rotation configurations.

        :param log_level: Log level (1-5 corresponding to DEBUG to CRITICAL).
        :param logger_name: Name of the logger (typically the module name).
        :param log_path: Path to the log file.
        :param show_threads: Whether to include thread information in the logs (default: True).
        :param backup_count: Number of backup log files to keep (default: 7).
        :param enable_file_logs: Whether to enable file logging (default: True).
        """
        self.logger = logging.getLogger(logger_name)
        self.log_level = self._get_log_level(log_level)
        self.show_threads = show_threads

        # This flag ensures set_log_level can only be used once
        self._log_level_finalized = False

        if not self.logger.hasHandlers():
            self._configure_logger(enable_file_logs, log_path, backup_count)

    def _get_log_level(self, log_level):
        """Return the logging level corresponding to the given log level number."""
        # Convert to int safely
        try:
            log_level_int = int(log_level)
        except ValueError:
            # Default to INFO if the conversion fails
            log_level_int = 2

        level_map = {
            1: logging.DEBUG,
            2: logging.INFO,
            3: logging.WARNING,
            4: logging.ERROR,
            5: logging.CRITICAL
        }
        return level_map.get(log_level_int, logging.INFO)

    def _configure_logger(self, enable_file_logs, log_path, backup_count):
        """Configure the logger with handlers and formatters."""
        self.logger.setLevel(self.log_level)

        # Add console handler
        self._add_console_handler()

        # Add file handler if enabled
        if enable_file_logs:
            if not self._validate_log_path(log_path):
                raise ValueError(f"Invalid log path: {log_path}")
            self._add_file_handler(log_path, backup_count)

        # Prevent propagation to the root logger
        self.logger.propagate = False

    def _add_console_handler(self):
        """Add a console handler with colored formatting."""
        console_handler = logging.StreamHandler()
        console_formatter = ColoredFormatter(fmt="%(asctime)s %(message)s", datefmt="[%d/%b/%Y:%H:%M:%S %z]", show_threads=self.show_threads)
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

    def _add_file_handler(self, log_path, backup_count):
        """Add a timed rotating file handler."""
        file_handler = TimedRotatingFileHandler(log_path, when='midnight', backupCount=backup_count)
        file_formatter = logging.Formatter(
            '%(asctime)s [%(threadName)s] %(levelname)s: %(message)s' if self.show_threads else
            '%(asctime)s %(levelname)s: %(message)s',
            datefmt='[%d/%b/%Y:%H:%M:%S %z]'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

    @staticmethod
    def _validate_log_path(log_path):
        """Ensure the directory for the log path exists and is writable."""
        if not log_path:
            raise ValueError("Log path cannot be empty.")

        # Expand and normalize the path
        log_path = os.path.abspath(log_path)
        log_dir = os.path.dirname(log_path)

        # If the log file is in the current directory
        if not log_dir:
            return True

        # Ensure the directory exists or create it
        if not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                raise ValueError(f"Could not create log directory '{log_dir}'. Error: {e}")

        # Ensure the path is a directory
        if not os.path.isdir(log_dir):
            raise ValueError(f"Log directory '{log_dir}' is not a directory.")

        # Ensure the directory is writable
        if not os.access(log_dir, os.W_OK):
            raise ValueError(f"Log directory '{log_dir}' is not writable.")

        return True

    def set_log_level(self, log_level):
        """
        Set the log level exactly once. Further attempts to call this method
        will raise an exception.

        :param log_level: New log level (1-5).
        :raises RuntimeError: If called more than once.
        """
        if self._log_level_finalized:
            raise RuntimeError("Log level can only be set once after initialization.")

        new_level = self._get_log_level(log_level)
        self.logger.setLevel(new_level)
        self.log_level = new_level
        self._log_level_finalized = True
        self.logger.debug(f"Log level updated to {logging.getLevelName(new_level)} (this can only be done once).")