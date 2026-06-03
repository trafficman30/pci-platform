#!/bin/bash
# Kill existing pci tmux session if running
tmux kill-session -t pci 2>/dev/null

# Create new session
tmux new-session -d -s pci -n iobus

# Start each service in its own window
tmux send-keys -t pci:iobus "PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.iobus.server" Enter

tmux new-window -t pci -n mova
tmux send-keys -t pci:mova "PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.mova.kernel_main 0" Enter

tmux new-window -t pci -n ug405
tmux send-keys -t pci:ug405 "PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.ug405.service" Enter

tmux new-window -t pci -n rtig
tmux send-keys -t pci:rtig "PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.rtig.service" Enter

tmux new-window -t pci -n autodim
tmux send-keys -t pci:autodim "PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.autodim.service" Enter

tmux new-window -t pci -n offline
tmux send-keys -t pci:offline "PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.offline.service" Enter

tmux new-window -t pci -n web
tmux send-keys -t pci:web "PYTHONPATH=/opt PCI_WEB_PORT=8082 /opt/pci/venv/bin/python -m pci.web.web_main" Enter

tmux attach -t pci
