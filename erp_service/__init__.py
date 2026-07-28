"""ERP service: the real (SQLAlchemy + SQLite backed) back-office system.

Outside `src/`: this is the business system the platform talks to over HTTP via
`infrastructure.connectors.rest.rest_connector.RestConnector` -- it is not part of the
platform itself. Business entities (`ServiceRequest`, `Appointment`) live here and only
here; the platform sends and receives plain dicts/JSON.
"""
