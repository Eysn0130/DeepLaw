/* Owner-invoked capability probe. Never subscribes to events or launches a Host. */
#include <EndpointSecurity/EndpointSecurity.h>
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

static const char *reason(es_new_client_result_t result) {
    switch (result) {
    case ES_NEW_CLIENT_RESULT_SUCCESS: return "available";
    case ES_NEW_CLIENT_RESULT_ERR_INVALID_ARGUMENT: return "invalid_argument";
    case ES_NEW_CLIENT_RESULT_ERR_INTERNAL: return "internal_error";
    case ES_NEW_CLIENT_RESULT_ERR_NOT_ENTITLED: return "endpoint_security_entitlement_missing";
    case ES_NEW_CLIENT_RESULT_ERR_NOT_PERMITTED: return "endpoint_security_tcc_not_permitted";
    case ES_NEW_CLIENT_RESULT_ERR_NOT_PRIVILEGED: return "endpoint_security_root_required";
    case ES_NEW_CLIENT_RESULT_ERR_TOO_MANY_CLIENTS: return "endpoint_security_client_limit";
    default: return "unknown_result";
    }
}

int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1) return 64;
    es_client_t *client = NULL;
    es_new_client_result_t result = es_new_client(&client,
        ^(es_client_t *c, const es_message_t *message) {
            /* No subscription is installed; do not inspect any process payload. */
            (void)c;
            (void)message;
        });
    int cleanup = client == NULL || es_delete_client(client) == ES_RETURN_SUCCESS;
    int descendants = dlsym(RTLD_DEFAULT, "es_new_descendants_client") != NULL;
    printf("{\"schema_version\":\"deeplaw.endpoint-security-capability/v1\","
           "\"client_result\":%d,\"reason_code\":\"%s\","
           "\"running_as_root\":%s,\"descendants_api_available\":%s,"
           "\"cleanup_confirmed\":%s,\"event_subscription_started\":false,"
           "\"host_started\":false,\"formal_admission\":false}\n",
           (int)result, reason(result), geteuid() == 0 ? "true" : "false",
           descendants ? "true" : "false", cleanup ? "true" : "false");
    return cleanup ? 0 : 1;
}
