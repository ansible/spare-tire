# spare-tire
Infrastructure for building Python wheels for testing with packages lacking wheels on tested platforms, and publishing
them to the S3-hosted spare-tire.testing.ansible.com package index.

`wheel_matrix.yml` lists the Python packages/versions, target core-ci instance types, and target Python/abi3 settings.
The first stage `gen_build_matrix.py` script consumes the wheel matrix and queries the S3 bucket behind the spare-tire
PyPI index at https://spare-tire.testing.ansible.com/simple. Any packages with version `latest` are resolved against
"real" PyPI to get the actual current latest version. It then generates a dynamic Azure Pipelines matrix with one
matrix node per build worker (all wheels for a given core-ci instance type are built in the same instance and matrix
entry). If no wheels need to be built, the pipeline short-circuits and ends execution.

Since Azure Pipelines matrices only allow one level of basic k/v config, the extra job data beyond the instance types
(eg, configs for all packages to be built on that instance) is smuggled to the later stages as embedded JSON in the
`job_data` key under each matrix entry. Each worker installs ansible-core and abuses ansible-test as a really expensive
core-ci client, and ansible as a templating engine. Ansible is not used for the actual remote wheel build operations in
order to keep readable and streaming output from the remote hosts. The AZP worker runs ansible-test against an
embedded collection test target in order to start a remote worker, then templates a bash script to execute the build
on the remote target VM and copy the built artifacts back to the CI worker, then attaches them as pipeline artifacts.

The final stage collects all pipeline artifacts, uploads them to the spare-tire S3 bucket, then regenerates the PyPI
index for the entire contents of the S3 bucket (including the new entries just added).
