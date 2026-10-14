# Cloud

Collects fine-grained signals across devices within a mesh network and centralizes access for agents via MCP.

# Features
1. Passively constructs atomic timeline of user activity containing VLM captioned screenshots and raw I/O
2. On-device capture services for Linux (Wayland)/Mac and web canvas for drawing
3. Manage context and chats via Electron or web app for desktop and mobile clients

# Hello world setup
0. Install + setup Tailscale on server and client devices
1. Clone the repo on a designated server machine and spin up the api
2. Install the Electron app on clients and give permissions to stalk yourself + configure clients to point to your server
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
