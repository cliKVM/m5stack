/* SPDX-License-Identifier: Apache-2.0
 * Android key wire layout follows AOSP libcrypto_utils/android_pubkey.cpp.
 * Independent implementation using mbedTLS; no private key is compiled into firmware.
 */
#include "clik_adb_auth.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "mbedtls/bignum.h"
#include "mbedtls/pk.h"
#include "mbedtls/rsa.h"
#include "mbedtls/platform_util.h"
#include "nvs.h"
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

static mbedtls_pk_context key;
static char public_key[768];
static bool initialized;
static int random_bytes(void *context, unsigned char *out, size_t length) {
    (void)context;
    esp_fill_random(out, length);
    /* Prime generation can be long on a microcontroller; let the idle task run.
     * This RNG is used only in task context, never in ISR or USB callbacks. */
    vTaskDelay(1);
    return 0;
}
static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
static esp_err_t encode_public_key(void) {
    uint8_t binary[524] = {0};
    mbedtls_mpi modulus, exponent, rr;
    mbedtls_mpi_init(&modulus); mbedtls_mpi_init(&exponent); mbedtls_mpi_init(&rr);
    int r = mbedtls_rsa_export(mbedtls_pk_rsa(key), &modulus, NULL, NULL, NULL, &exponent);
    if (!r && mbedtls_mpi_cmp_int(&exponent, 65537) != 0) r = -1;
    if (!r) r = mbedtls_mpi_write_binary_le(&modulus, binary + 8, 256);
    if (!r) r = mbedtls_mpi_lset(&rr, 1);
    if (!r) r = mbedtls_mpi_shift_l(&rr, 4096);
    if (!r) r = mbedtls_mpi_mod_mpi(&rr, &rr, &modulus);
    if (!r) r = mbedtls_mpi_write_binary_le(&rr, binary + 264, 256);
    if (!r) {
        uint32_t n0 = (uint32_t)binary[8] | ((uint32_t)binary[9] << 8) |
                      ((uint32_t)binary[10] << 16) | ((uint32_t)binary[11] << 24);
        uint32_t inverse = 1;
        for (unsigned i = 0; i < 5; ++i) inverse *= 2u - n0 * inverse;
        put32(binary, 64); put32(binary + 4, 0u - inverse); put32(binary + 520, 65537);
        size_t actual = 0;
        r = mbedtls_base64_encode((unsigned char *)public_key, sizeof(public_key) - 32,
                                 &actual, binary, sizeof(binary));
        if (!r) strcpy(public_key + actual, " clikVM@esp32s3");
    }
    mbedtls_mpi_free(&rr); mbedtls_mpi_free(&exponent); mbedtls_mpi_free(&modulus);
    return r == 0 ? ESP_OK : ESP_FAIL;
}
esp_err_t clik_adb_auth_init(void) {
    if (initialized) return ESP_OK;
    nvs_handle_t nvs;
    esp_err_t result = nvs_open("clik_adb", NVS_READWRITE, &nvs);
    if (result != ESP_OK) return result;
    unsigned char *der = calloc(1, 2048);
    if (!der) { nvs_close(nvs); return ESP_ERR_NO_MEM; }
    size_t length = 2048;
    result = nvs_get_blob(nvs, "rsa_der", der, &length);
    mbedtls_pk_init(&key);
    int crypto_error = 0;
    if (result == ESP_OK) {
        crypto_error = mbedtls_pk_parse_key(&key, der, length, NULL, 0, random_bytes, NULL);
        if (!crypto_error && (!mbedtls_pk_can_do(&key, MBEDTLS_PK_RSA) || mbedtls_pk_get_bitlen(&key) != 2048)) crypto_error = -1;
        if (!crypto_error) crypto_error = mbedtls_rsa_check_privkey(mbedtls_pk_rsa(key));
    } else if (result == ESP_ERR_NVS_NOT_FOUND) {
        crypto_error = mbedtls_pk_setup(&key, mbedtls_pk_info_from_type(MBEDTLS_PK_RSA));
        if (!crypto_error) crypto_error = mbedtls_rsa_gen_key(mbedtls_pk_rsa(key), random_bytes, NULL, 2048, 65537);
        int written = crypto_error ? -1 : mbedtls_pk_write_key_der(&key, der, 2048);
        if (written <= 0) crypto_error = -1;
        if (!crypto_error) {
            result = nvs_set_blob(nvs, "rsa_der", der + 2048 - written, (size_t)written);
            if (result == ESP_OK) result = nvs_commit(nvs);
        }
    }
    mbedtls_platform_zeroize(der, 2048);
    free(der); nvs_close(nvs);
    if (crypto_error || result != ESP_OK) { mbedtls_pk_free(&key); return crypto_error ? ESP_FAIL : result; }
    result = encode_public_key();
    if (result != ESP_OK) { mbedtls_pk_free(&key); return result; }
    initialized = true;
    return ESP_OK;
}
esp_err_t clik_adb_auth_sign(const uint8_t token[20], uint8_t signature[256]) {
    if (!initialized || !token || !signature) return ESP_ERR_INVALID_STATE;
    int result = mbedtls_rsa_rsassa_pkcs1_v15_sign(mbedtls_pk_rsa(key), random_bytes, NULL,
                                                MBEDTLS_MD_SHA1, 20, token, signature);
    return result == 0 ? ESP_OK : ESP_FAIL;
}
const char *clik_adb_auth_public_key(void) { return initialized ? public_key : NULL; }
