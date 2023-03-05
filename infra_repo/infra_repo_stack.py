from aws_cdk import (
    Stack,
    aws_codepipeline as codepipeline,
    aws_codebuild as codebuild,
    aws_codepipeline_actions as codepipeline_actions,
    aws_eks as eks,
    aws_ecr as ecr,
    aws_iam as iam,
    SecretValue,
)
from constructs import Construct


class ApplicationStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """
        This stack creates the following resources per https://tinyurl.com/89zxu7at:
        1. EKS cluster where the application is deployed. See: https://docs.aws.amazon.com/cdk/api/v1/docs/aws-eks-readme.html
        2. ECR as repo for docker images. See: https://docs.aws.amazon.com/cdk/api/v1/docs/aws-ecr-readme.html
        3. CodePipeline triggered by Git Webhooks with 3 phases:
            a. Docker Build for the application [AWS CodeBuild].
            b. Manual Approval to deploy.
            c. Deploy using kubectl [AWS CodeBuild]
            For CodePipeline ref. see: https://docs.aws.amazon.com/cdk/api/v1/docs/aws-codepipeline-readme.html
            For CodeBuild ref. see https://docs.aws.amazon.com/cdk/api/v1/docs/aws-codebuild-readme.html
        """
        super().__init__(scope, construct_id, **kwargs)

        eks_master_role = iam.Role(self, id=f"eks-application-role", assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"))
        eks_cluster = eks.Cluster(self, f"application-cluster", version=eks.KubernetesVersion.V1_23, masters_role=eks_master_role)

        docker_repo = ecr.Repository(
            scope=self,
            id=f"application-repo",
            repository_name=f"application-repo"
        )

        application_pipeline = codepipeline.Pipeline(
            scope=self,
            id=f"application-pipeline",
            pipeline_name=f"application-pipeline"
        )

        github_trigger_output = codepipeline.Artifact()
        build_output = codepipeline.Artifact()
        deploy_output = codepipeline.Artifact()

        ci_env_variables = dict(
                REPO_ECR=codebuild.BuildEnvironmentVariable(
                    value=docker_repo.repository_uri)
            )

        ci_pipeline = self.create_codebuild_pipeline_project("DockerBuild", ci_env_variables, "build-pipeline.yml")

        cd_env_variables = dict(
                REPO_ECR=codebuild.BuildEnvironmentVariable(
                    value=docker_repo.repository_uri),
                EKS_CLUSTER_NAME=codebuild.BuildEnvironmentVariable(
                    value=eks_cluster.cluster_name)
                EKS_ROLE=codebuild.BuildEnvironmentVariable(
                    value=eks_master_role.role_arn)
            )
        cd_pipeline = self.create_codebuild_pipeline_project("Deploy", cd_env_variables, "deploy-pipeline.yml")

        docker_repo.grant_pull_push(ci_pipeline)

        # GitHub webhook to trigger AWS pipeline
        pipeline_trigger = codepipeline_actions.GitHubSourceAction(
            oauth_token=SecretValue.secrets_manager("github"),
            action_name="GitCommit_Source",
            owner="HiHashoom",
            repo="application",
            branch="main",
            output=github_trigger_output,
        )

        application_pipeline.add_stage(
            stage_name="GitWebhook",
            actions=[pipeline_trigger]
        )

        # Phase (1) build and push the docker image to ECR
        application_pipeline.add_stage(
            stage_name="DockerBuildAndPush",
            actions=[
                codepipeline_actions.CodeBuildAction(
                    action_name="DockerBuildAndPush",
                    project=ci_pipeline,
                    input=github_trigger_output,
                    outputs=[build_output]
                )
            ]
        )

        # Phase (2) manual review and approval
        pipeline_manual_approval = codepipeline_actions.ManualApprovalAction(
            additional_information=f"CommitId: {pipeline_trigger.variables.commit_id}",
            action_name="ManualApproval",
            external_entity_link=pipeline_trigger.variables.commit_url

        )
        application_pipeline.add_stage(
            stage_name="ManualApproval",
            actions=[pipeline_manual_approval]
        )

        eks_master_role.grant_assume_role(cd_pipeline.role)
        eks_master_role.assume_role_policy.add_statements(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["sts:AssumeRole"],
            principals=[cd_pipeline.role]
        ))

        eks_master_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["eks:DescribeCluster"],
            resources=[eks_cluster.cluster_arn]
        ))

        # Phase (3) deploy the latest image
        application_pipeline.add_stage(
            stage_name="AppDeploy",
            actions=[
                codepipeline_actions.CodeBuildAction(
                    action_name="AppDeploy",
                    project=cd_pipeline,
                    input=github_trigger_output,
                    outputs=[deploy_output]
                )
            ]
        )

    def create_codebuild_pipeline_project(self, project_id: str, env_variables: dict[str:str], build_spec_file_name: str) -> codebuild.PipelineProject:
        return codebuild.PipelineProject(
            scope=self,
            id=project_id,
            environment=dict(
                build_image=codebuild.LinuxBuildImage.AMAZON_LINUX_2_3,
                privileged=True),
            environment_variables=env_variables,
            build_spec=codebuild.BuildSpec.from_source_filename(build_spec_file_name)
        )
