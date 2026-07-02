#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
source ~/.zprofile 2>/dev/null
cd ~/.agent-ref-pipeline || exit 1
/Users/hh/.agent-ref-pipeline/venv/bin/python3.14 process_queue.py >> logs/processor.log 2>&1
