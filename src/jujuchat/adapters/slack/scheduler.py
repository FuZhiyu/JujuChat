#%% Scheduled Message System
"""
Scheduled message system for the Slack Claude Bot.

This module provides automatic message scheduling capabilities that integrate
with the existing Claude session infrastructure. Messages can be scheduled
using cron-like expressions and will be sent to specified Slack channels.
"""

import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path

from .logger import BotLogger
from .exceptions import BotError


@dataclass
class ScheduledMessage:
    """
    Configuration for a scheduled message.
    
    Attributes:
        name: Unique identifier for the scheduled message
        time: Cron-like time expression (e.g., "0 9 * * 1-5" for 9 AM weekdays)
        channel: Slack channel ID to send message to
        prompt: Message content/prompt to send to Claude
        enabled: Whether this scheduled message is active
        timezone: Timezone for scheduling (defaults to system timezone)
        last_run: Timestamp of last execution (managed internally)
        next_run: Timestamp of next scheduled execution (managed internally)
    """
    name: str
    time: str  # Cron expression
    channel: str
    prompt: str
    enabled: bool = True
    timezone: Optional[str] = None
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None


class CronParser:
    """
    Simple cron expression parser for scheduled messages.
    
    Supports basic cron format: minute hour day_of_month month day_of_week
    Examples:
    - "0 9 * * *" = 9:00 AM every day
    - "0 9 * * 1-5" = 9:00 AM Monday-Friday
    - "30 17 * * 5" = 5:30 PM every Friday
    """
    
    @staticmethod
    def parse_cron(cron_expr: str) -> Dict[str, Any]:
        """Parse cron expression into components."""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}. Expected 5 parts (minute hour day month dow)")
        
        minute, hour, day, month, dow = parts
        
        return {
            'minute': CronParser._parse_field(minute, 0, 59),
            'hour': CronParser._parse_field(hour, 0, 23),
            'day': CronParser._parse_field(day, 1, 31),
            'month': CronParser._parse_field(month, 1, 12),
            'dow': CronParser._parse_field(dow, 0, 6)  # 0=Sunday, 6=Saturday
        }
    
    @staticmethod
    def _parse_field(field: str, min_val: int, max_val: int) -> List[int]:
        """Parse individual cron field."""
        if field == '*':
            return list(range(min_val, max_val + 1))
        
        values = []
        for part in field.split(','):
            if '-' in part:
                # Range like "1-5"
                start, end = map(int, part.split('-'))
                values.extend(range(start, end + 1))
            elif '/' in part:
                # Step like "*/2" or "0-23/2"
                base, step = part.split('/')
                if base == '*':
                    base_values = list(range(min_val, max_val + 1))
                else:
                    base_values = CronParser._parse_field(base, min_val, max_val)
                values.extend([v for v in base_values if v % int(step) == 0])
            else:
                # Single value
                values.append(int(part))
        
        # Filter and sort values
        values = [v for v in values if min_val <= v <= max_val]
        return sorted(list(set(values)))
    
    @staticmethod
    def next_run_time(cron_expr: str, from_time: Optional[datetime] = None) -> datetime:
        """Calculate next run time for a cron expression."""
        if from_time is None:
            from_time = datetime.now()
        
        parsed = CronParser.parse_cron(cron_expr)
        
        # Start checking from the next minute
        next_time = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        
        # Check up to 4 years in the future (prevent infinite loops)
        max_iterations = 366 * 24 * 60 * 4  # 4 years in minutes
        iterations = 0
        
        while iterations < max_iterations:
            if (next_time.minute in parsed['minute'] and
                next_time.hour in parsed['hour'] and
                next_time.day in parsed['day'] and
                next_time.month in parsed['month'] and
                next_time.weekday() + 1 in [d if d != 0 else 7 for d in parsed['dow']]):  # Convert Python weekday to cron
                return next_time
            
            next_time += timedelta(minutes=1)
            iterations += 1
        
        raise ValueError(f"Could not find next run time for cron expression: {cron_expr}")


class AsyncScheduler:
    """
    Asynchronous scheduler for managing scheduled messages.
    
    Integrates with the existing bot infrastructure to send scheduled messages
    through Claude sessions without conflicts.
    """
    
    def __init__(self, bot_config, message_processor, logger: BotLogger, slack_app=None):
        """
        Initialize scheduler with bot dependencies.
        
        Args:
            bot_config: Complete bot configuration
            message_processor: MessageProcessor instance for sending messages
            logger: BotLogger instance for logging
            slack_app: Slack app instance for sending messages (optional)
        """
        self.bot_config = bot_config
        self.message_processor = message_processor
        self.logger = logger
        self.slack_app = slack_app
        self.scheduled_messages: Dict[str, ScheduledMessage] = {}
        self.running = False
        self.scheduler_task: Optional[asyncio.Task] = None
        self._state_lock = asyncio.Lock()
        
    def load_scheduled_messages(self, schedule_config: Dict[str, Any]) -> None:
        """
        Load scheduled messages from configuration.
        
        Args:
            schedule_config: Dictionary of scheduled message configurations
        """
        self.scheduled_messages.clear()
        
        for name, config in schedule_config.items():
            try:
                scheduled_msg = ScheduledMessage(
                    name=name,
                    time=config['time'],
                    channel=config['channel'],
                    prompt=config['prompt'],
                    enabled=config.get('enabled', True),
                    timezone=config.get('timezone')
                )
                
                # Calculate initial next run time
                if scheduled_msg.enabled:
                    scheduled_msg.next_run = CronParser.next_run_time(scheduled_msg.time)
                
                self.scheduled_messages[name] = scheduled_msg
                print(f"✓ Loaded scheduled message: {name} -> {scheduled_msg.next_run}")
                
            except Exception as e:
                print(f"⚠️  Failed to load scheduled message '{name}': {e}")
    
    async def start(self) -> None:
        """Start the scheduler background task."""
        if self.running:
            return
        
        self.running = True
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        print("✅ Scheduler started")
    
    async def stop(self) -> None:
        """Stop the scheduler background task."""
        self.running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        print("🛑 Scheduler stopped")
    
    async def _scheduler_loop(self) -> None:
        """Main scheduler loop that checks for and executes scheduled messages."""
        retry_count = 0
        max_retries = 5
        
        while self.running and retry_count <= max_retries:
            try:
                current_time = datetime.now()
                
                # Check each scheduled message
                for name, scheduled_msg in self.scheduled_messages.items():
                    if not scheduled_msg.enabled:
                        continue
                    
                    if scheduled_msg.next_run and current_time >= scheduled_msg.next_run:
                        await self._execute_scheduled_message(scheduled_msg)
                
                # Reset retry count on successful loop
                retry_count = 0
                
                # Dynamic sleep based on next scheduled event
                enabled_messages = [msg for msg in self.scheduled_messages.values() if msg.enabled and msg.next_run]
                if enabled_messages:
                    next_check = min(msg.next_run for msg in enabled_messages)
                    sleep_seconds = max((next_check - current_time).total_seconds(), 30)
                    sleep_seconds = min(sleep_seconds, 300)  # Max 5 minutes
                else:
                    sleep_seconds = 300  # Default 5 minutes if no enabled messages
                
                await asyncio.sleep(sleep_seconds)
                
            except asyncio.CancelledError:
                print("📅 Scheduler loop cancelled - shutting down cleanly")
                break
            except (ConnectionError, TimeoutError) as e:
                retry_count += 1
                print(f"⚠️  Network error in scheduler loop (attempt {retry_count}/{max_retries}): {e}")
                try:
                    await self.logger.log_error("scheduler", "system", f"Network error: {str(e)}")
                except Exception:
                    pass
                
                # Exponential backoff for network errors
                backoff_time = min(60 * retry_count, 300)
                await asyncio.sleep(backoff_time)
                
            except Exception as e:
                retry_count += 1
                print(f"⚠️  Unexpected error in scheduler loop (attempt {retry_count}/{max_retries}): {e}")
                try:
                    await self.logger.log_error("scheduler", "system", f"Unexpected error: {str(e)}")
                except Exception:
                    pass
                
                if retry_count > max_retries:
                    print(f"❌ Scheduler stopped after {max_retries} consecutive failures")
                    self.running = False
                    break
                
                # Linear backoff for unexpected errors
                await asyncio.sleep(60)
        
        if retry_count > max_retries:
            print("❌ Scheduler loop exited due to too many consecutive errors")
    
    async def _execute_scheduled_message(self, scheduled_msg: ScheduledMessage) -> None:
        """
        Execute a scheduled message by sending it through the message processor.
        
        Args:
            scheduled_msg: The scheduled message to execute
        """
        try:
            print(f"📅 Executing scheduled message: {scheduled_msg.name}")
            
            # Create a system user context for scheduled messages
            system_user_name = "Scheduler"
            system_user_id = "SYSTEM_SCHEDULER"
            
            # Process template variables in the prompt
            processed_prompt = self._process_prompt_template(scheduled_msg.prompt)
            
            # Add scheduling context to the prompt
            contextual_prompt = f"[Scheduled Message: {scheduled_msg.name}]\n{processed_prompt}"
            
            # Send message through existing message processor infrastructure
            # This ensures it uses the same session management and Claude integration
            response = await self.message_processor.process_message(
                text=contextual_prompt,
                channel=scheduled_msg.channel,
                user_name=system_user_name,
                user_id=system_user_id
            )
            
            # Send the response to Slack
            await self._send_to_slack(scheduled_msg.channel, response)
            
            # Update scheduling info
            scheduled_msg.last_run = datetime.now()
            scheduled_msg.next_run = CronParser.next_run_time(scheduled_msg.time)
            
            # Log successful execution
            await self.logger.log_message(
                system_user_id, 
                scheduled_msg.channel, 
                f"Scheduled message '{scheduled_msg.name}' executed successfully", 
                "scheduled_execution"
            )
            
            print(f"✅ Scheduled message '{scheduled_msg.name}' executed. Next run: {scheduled_msg.next_run}")
            
        except Exception as e:
            error_msg = f"Failed to execute scheduled message '{scheduled_msg.name}': {str(e)}"
            print(f"❌ {error_msg}")
            
            # Log error
            try:
                await self.logger.log_error("SYSTEM_SCHEDULER", scheduled_msg.channel, error_msg)
            except Exception:
                pass  # Don't let logging errors break execution
            
            # Still update next run time to avoid repeated failures
            try:
                scheduled_msg.next_run = CronParser.next_run_time(scheduled_msg.time)
            except Exception as next_run_error:
                print(f"❌ Failed to calculate next run time: {next_run_error}")
                scheduled_msg.enabled = False  # Disable if we can't calculate next run
    
    async def _send_to_slack(self, channel: str, message: str) -> None:
        """
        Send message to Slack channel.
        
        Args:
            channel: Slack channel ID
            message: Message content to send
        """
        try:
            if self.slack_app and self.slack_app.client:
                # Send message to Slack using the app's client
                await self.slack_app.client.chat_postMessage(
                    channel=channel,
                    text=message
                )
                print(f"📤 Sent scheduled message to {channel}: {message[:100]}{'...' if len(message) > 100 else ''}")
            else:
                # Fallback: simulate sending if no app available
                print(f"📤 [SIMULATED] Sending to {channel}: {message[:100]}{'...' if len(message) > 100 else ''}")
            
            # Log the outgoing scheduled message
            await self.logger.log_message(
                "SYSTEM_SCHEDULER", 
                channel, 
                message, 
                "outgoing_scheduled"
            )
        except Exception as e:
            error_msg = f"Failed to send scheduled message to {channel}: {str(e)}"
            print(f"❌ {error_msg}")
            raise BotError(error_msg)
    
    def get_schedule_status(self) -> Dict[str, Any]:
        """
        Get current status of all scheduled messages.
        
        Returns:
            Dictionary with schedule status information
        """
        status = {
            'running': self.running,
            'total_messages': len(self.scheduled_messages),
            'enabled_messages': len([m for m in self.scheduled_messages.values() if m.enabled]),
            'messages': {}
        }
        
        for name, msg in self.scheduled_messages.items():
            status['messages'][name] = {
                'enabled': msg.enabled,
                'channel': msg.channel,
                'next_run': msg.next_run.isoformat() if msg.next_run else None,
                'last_run': msg.last_run.isoformat() if msg.last_run else None,
                'cron_expression': msg.time
            }
        
        return status
    
    async def add_scheduled_message(self, name: str, time: str, channel: str, prompt: str, enabled: bool = True) -> None:
        """
        Add a new scheduled message at runtime.
        
        Args:
            name: Unique identifier for the message
            time: Cron expression for scheduling
            channel: Slack channel ID
            prompt: Message prompt
            enabled: Whether the message is active
        """
        try:
            scheduled_msg = ScheduledMessage(
                name=name,
                time=time,
                channel=channel,
                prompt=prompt,
                enabled=enabled
            )
            
            if enabled:
                scheduled_msg.next_run = CronParser.next_run_time(time)
            
            async with self._state_lock:
                self.scheduled_messages[name] = scheduled_msg
            print(f"✅ Added scheduled message: {name}")
            
        except Exception as e:
            raise BotError(f"Failed to add scheduled message '{name}': {str(e)}")
    
    async def remove_scheduled_message(self, name: str) -> bool:
        """
        Remove a scheduled message.
        
        Args:
            name: Name of the scheduled message to remove
            
        Returns:
            True if message was removed, False if not found
        """
        async with self._state_lock:
            if name in self.scheduled_messages:
                del self.scheduled_messages[name]
                print(f"✅ Removed scheduled message: {name}")
                return True
            return False
    
    async def enable_scheduled_message(self, name: str) -> bool:
        """
        Enable a scheduled message.
        
        Args:
            name: Name of the scheduled message to enable
            
        Returns:
            True if message was enabled, False if not found
        """
        async with self._state_lock:
            if name in self.scheduled_messages:
                self.scheduled_messages[name].enabled = True
                self.scheduled_messages[name].next_run = CronParser.next_run_time(self.scheduled_messages[name].time)
                print(f"✅ Enabled scheduled message: {name}")
                return True
            return False
    
    async def disable_scheduled_message(self, name: str) -> bool:
        """
        Disable a scheduled message.
        
        Args:
            name: Name of the scheduled message to disable
            
        Returns:
            True if message was disabled, False if not found
        """
        async with self._state_lock:
            if name in self.scheduled_messages:
                self.scheduled_messages[name].enabled = False
                self.scheduled_messages[name].next_run = None
                print(f"✅ Disabled scheduled message: {name}")
                return True
            return False
    
    def _process_prompt_template(self, prompt: str) -> str:
        """
        Process template variables in scheduled message prompts.
        
        Supported variables:
        - {date} - Current date (YYYY-MM-DD)
        - {time} - Current time (HH:MM:SS)
        - {datetime} - Current date and time (YYYY-MM-DD HH:MM:SS)
        - {timestamp} - ISO timestamp (YYYY-MM-DDTHH:MM:SS)
        - {weekday} - Day of week (Monday, Tuesday, etc.)
        - {month} - Month name (January, February, etc.)
        - {year} - Current year (YYYY)
        
        Args:
            prompt: Raw prompt template with variables
            
        Returns:
            Processed prompt with variables substituted
        """
        now = datetime.now()
        
        # Define template variables
        template_vars = {
            'date': now.strftime('%Y-%m-%d'),
            'time': now.strftime('%H:%M:%S'),
            'datetime': now.strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': now.isoformat(),
            'weekday': now.strftime('%A'),
            'month': now.strftime('%B'),
            'year': now.strftime('%Y'),
            'day': now.strftime('%d'),
            'hour': now.strftime('%H'),
            'minute': now.strftime('%M'),
            'second': now.strftime('%S')
        }
        
        # Replace template variables
        processed_prompt = prompt
        for var_name, var_value in template_vars.items():
            processed_prompt = processed_prompt.replace(f'{{{var_name}}}', var_value)
        
        return processed_prompt