"""
Feature: system_monitor_ascii
Description: Monitors and displays current CPU and RAM utilization using ASCII progress bars. Useful for quick system resource checks.
Autonomous Evolutionary Capability synthesized by NIGHTFALL AI.
"""

FEATURE_METADATA = {'name': 'system_monitor_ascii', 'aliases': ['monitor system', 'system status', 'resource usage', 'systemmonitorascii'], 'description': 'Monitors and displays current CPU and RAM utilization using ASCII progress bars. Useful for quick system resource checks.', 'triggers': ['monitor system', 'system status', 'resource usage', 'show cpu and ram', 'what is my system load', 'Monitor CPU and RAM load and display an ASCII visual progress bar for both CPU utilization and RAM usage.', 'monitor cpu and ram load and display an ascii visual progress bar for both cpu utilization and ram usage.', 'system monitor ascii'], 'parameters': {}, 'created_at': 1790779504.7412686, 'version': '1.0.0', 'author': 'Project Ultron Autonomous Self-Evolution Engine', 'active': True}

import psutil
import os
import time
from typing import Dict, Any

def execute(**kwargs) -> Dict[str, Any]:
    """Monitors CPU and RAM load and displays an ASCII visual progress bar for both."""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        mem_info = psutil.virtual_memory()
        ram_percent = mem_info.percent

        def create_progress_bar(percentage: float, width: int = 50) -> str:
            """Creates an ASCII progress bar."""
            filled_width = int(width * (percentage / 100))
            bar = '#' * filled_width + '-' * (width - filled_width)
            return f'|{bar}| {percentage:.1f}%'

        cpu_bar = create_progress_bar(cpu_percent)
        ram_bar = create_progress_bar(ram_percent)

        output = f"CPU Usage: {cpu_bar}\nRAM Usage: {ram_bar}"

        return {
            'title': 'System Resource Monitor',
            'summary': output
        }

    except Exception as e:
        # Fallback in case of any error (e.g., psutil not installed, permissions)
        error_message = f"Could not retrieve system metrics. Error: {e}"
        return {
            'title': 'System Monitor Error',
            'summary': error_message
        }

if __name__ == '__main__':
    # Example of how to run the function directly for testing
    # This part is not included in the final skill code but useful for local development
    print("Running system monitor...")
    result = execute()
    print(result)
