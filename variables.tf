variable "sqs_dlq_arn"{
    description = "Dead Letter Queue for on_failure delivery of invocations"
    type = string
    
}
variable "kms_key_arn"{
    description = "KMS key to use for encryption"
    type = string
}

variable "sqs_opsgenie_url"{
    description = "SQS URL "
    type = string
    default = "https://sqs.eu-central-1.amazonaws.com/611159992020/sqs-opsgenie-lambda-queue-20220711145511259200000002"
}
   
variable "lambda_name" {
    type = string
    default = "slack2opsgenie"
}