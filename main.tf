
module "lambda_slack2opsgenie" {
  source = "github.com/wearetechnative/terraform-aws-lambda.git?ref=fe102f9e43209b47bf919be75066df102458d8d9"
  name              = var.lambda_name
  role_arn          = module.iam_role_slack2opsgenie.role_arn
  role_arn_provided = true
  kms_key_arn       = var.kms_key_arn
  handler     = "slack2opsgenie.handler"
  memory_size = 512
  timeout     = 600
  runtime     = "python3.12"

  source_type               = "local"
  source_directory_location = "${path.module}/slack2opsgenie_lambda"
  source_file_name          = null
  sqs_dlq_arn = var.sqs_dlq_arn
  environment_variables = {
    BOT_USER_TOKEN = data.aws_ssm_parameter.bot_user_token.value 
    SLACK_SIGNING_SECRET = data.aws_ssm_parameter.slack_signing_secret.value
    SQS_URL = var.sqs_opsgenie_url
  }
} 

resource "aws_lambda_function_url" "slack2opsgenie_functionurl" {
  function_name      = module.lambda_slack2opsgenie.lambda_function_name
  authorization_type = "NONE"
}
#creates a iam role for lambda that sends the message with proper keyword in slack channel to opsgenie to create an alert 
module "iam_role_slack2opsgenie" {
  source    = "github.com/wearetechnative/terraform-aws-iam-role.git?ref=9229bbd0280807cbc49f194ff6d2741265dc108a"
  role_name = "iam_role-${var.lambda_name}"
  role_path = "/"

  customer_managed_policies = {
    "instance_scheduler" : jsondecode(data.aws_iam_policy_document.slack2opsgenie.json)
  }

  trust_relationship = {
    "lambda" : { "identifier" : "lambda.amazonaws.com", "identifier_type" : "Service", "enforce_mfa" : false, "enforce_userprincipal" : false, "external_id" : null, "prevent_account_confuseddeputy" : false }
  }
}

#IAM policy document that has SQS and LOG access
data "aws_iam_policy_document" "slack2opsgenie" {
  statement {
    sid = "LogAccess"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
  statement {
    sid = "SQSAccess"
    actions = [
        "sqs:SendMessage"
    ]
    resources = ["*"]
  }
}

resource "aws_kms_grant" "a" {
  name              = "my-grant"
  key_id            = var.kms_key_arn
  grantee_principal = module.iam_role_slack2opsgenie.role_arn
  operations        = ["Encrypt", "Decrypt", "GenerateDataKey"]
}

data "aws_ssm_parameter" "bot_user_token"{
    name = "slack_api_app_slack2opsgenie_BOT_user_OAuth_Token"
    with_decryption = true
}

data "aws_ssm_parameter" "slack_signing_secret"{
    name = "slack_api_app_slack2opsgenie_signing_secret"
    with_decryption = true
}