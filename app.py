#!/usr/bin/env python3

import aws_cdk as cdk

from infra_repo.infra_repo_stack import ApplicationStack

app = cdk.App()
prod_env = cdk.Environment(region="us-east-2")
ApplicationStack(app, "ApplicationProduction", env=prod_env)

app.synth()
