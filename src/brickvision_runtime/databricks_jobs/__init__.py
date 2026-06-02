"""Job-runner entry-points deployed to Databricks via DAB.

Each module here is a ``spark_python_task`` referenced by an
install-step Job spec. They are kept import-light: the Databricks
SDK + Spark sessions are loaded lazily inside ``main()`` so unit
tests can import the modules without a workspace.
"""

__all__: list[str] = []
