#!/usr/bin/env python3
"""
Eventlet monkey patching for SocketIO compatibility.
This MUST be imported before any other modules to work properly.
"""

import eventlet
# Apply monkey patching before importing anything else
eventlet.monkey_patch()

# Now safe to import Flask and other modules