"""
Entry point for JujuChat Slack adapter.

Usage:
    python -m jujuchat.adapters.slack
    python -m jujuchat.adapters.slack /path/to/project
"""

import sys
import asyncio
from pathlib import Path

def main():
    """Main entry point for Slack adapter."""
    import os
    from dotenv import load_dotenv
    
    # Handle optional project path argument
    project_path = os.getcwd()
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        print(f"Using project path from argument: {project_path}")
        os.chdir(project_path)
    else:
        print(f"Using current working directory: {project_path}")
    
    # Load environment variables
    load_dotenv()
    
    # Load configuration before importing bot
    from .config import load_config
    config = load_config()
    
    # Import bot with config already loaded
    import jujuchat.adapters.slack.bot as bot_module
    bot_module.config = config
    
    # Run the bot
    asyncio.run(bot_module.main())

if __name__ == "__main__":
    main()