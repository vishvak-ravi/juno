# Cloud

Collects fine-grained signals across devices within a mesh network and centralizes access for agents via MCP.

# Features
1. Passively constructs atomic timeline of user activity containing VLM captioned screenshots and raw I/O
2. On-device capture services for Linux (Wayland)/Mac and web canvas for drawing
3. Manage context and chats via Electron or web app for desktop and mobile clients

# Commands

| | Server (macOS, Linux) | Client (macOS, Linux) |
|---|---|---|
| Install | `curl -fsSL https://raw.githubusercontent.com/vishvak-ravi/juno/main/scripts/install-server.sh \| bash` | `curl -fsSL https://raw.githubusercontent.com/vishvak-ravi/juno/main/scripts/install-client.sh \| bash` |
| Start | `cd ~/juno && uv run python -m server` | `cd ~/juno/apps/desktop && npm start` |
| Tests | `cd ~/juno && uv run pytest` | |

The client starts the recorder; its Status page shows whether this computer is recording.

# Hello world setup
0. Install + setup Tailscale on server and client devices
1. Run the server install on a designated server machine and start it; it prints the address clients connect to
2. Run the client install on each computer, grant the capture permissions it asks for, choose whether to install the passive agent module (macOS: camera gestures and dictation) and give it the server's address
3. Ask Cloud 'what have I been up to'

## Optional setup
### Cloud agent
Point the agent to a model endpoint you trust with your context

### Mobile 'app'
The server hosts a mobile native web-app ```<your-server-url> ```. You can manage chats and timeline from here.

### Drawing 'app'
Whiteboarding on a tablet can be tracked by navigating to the server's ```<your-server-url>/draw```

# Coming soon (hopefully)
1. More safety: PII identification BEFORE storing to timeline, user-defined sanitization/censorship for API responses
2. More functionality leveraging intimate context: intent prediction, prompt enhancement via contextualization
3. More real-time sensors: camera, microphone
4. More semantic app-owned memory: graphia/KG/long-term memory? reading list? TODO list? long term goal tracking?
5. More verifiable pilot + system improvement: long + short term task performance (system, pilot, both) across time to complete, token spend (artifacts/methods: auto-collected datasets; detected tasks + relevant event log with model attribution + implicit human preference signals + proactive confidence-based label correction for explicit signals; auto-training)
6. More proactivity: feeds (human/agent messages, content), see 2.
