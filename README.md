# Photon Voice Accessibility

A voice agent for Photon (Neutron) pharmacy patients — outbound order calls and
an inbound front desk that schedules callbacks. Two equivalent implementations:

- **[Python/](Python/)** — reference implementation. See its README for setup,
  architecture diagrams, and Railway deploy.
- **[TypeScript/](TypeScript/)** — Node clone of the same app.

Pick a folder and follow its README. Both deploy on Railway as **two services**
(a public webhook server + a LiveKit worker) and give Neutron one webhook URL.
