# quantum-mqtt-client
quantum telemetry mqtt client pulling data from TTN

# Payload Layout and Versioning Concept

The first payload byte uniquely identifies the payload format version. This approach allows for flexible evolution of the payload structure while maintaining backward compatibility and clear parsing rules for consumers.

[View the JSON payload layout mapping for v1](./config/payload_layout.json)

