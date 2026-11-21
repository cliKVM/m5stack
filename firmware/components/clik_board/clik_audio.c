/* SPDX-License-Identifier: Apache-2.0
 * Codec setup follows Espressif ESP-BSP and Waveshare reference pin mappings.
 */
#include "clik_board.h"
#include "board_private.h"
#include "driver/i2s_std.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
static const char *TAG = "clik_audio";
static esp_codec_dev_handle_t mic, speaker;
static SemaphoreHandle_t audio_mutex;

esp_err_t clik_board_audio_init(void) {
    if (mic && speaker)
        return ESP_OK;
    ESP_RETURN_ON_ERROR(clik_board_audio_power(), TAG, "Audio power");
    audio_mutex = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(audio_mutex, ESP_ERR_NO_MEM, TAG, "Audio mutex");
    i2s_chan_handle_t tx = NULL, rx = NULL;
    i2s_chan_config_t ch = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ch.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&ch, &tx, &rx), TAG, "I2S duplex");
    i2s_std_config_t cfg = {.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
                            .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                                I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
                            .gpio_cfg = {.mclk = CLIK_I2S_MCLK,
                                         .bclk = CLIK_I2S_BCLK,
                                         .ws = CLIK_I2S_WS,
                                         .dout = CLIK_I2S_DOUT,
                                         .din = CLIK_I2S_DIN}};
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(tx, &cfg), TAG, "TX init");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(rx, &cfg), TAG, "RX init");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(tx), TAG, "TX enable");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(rx), TAG, "RX enable");
    audio_codec_i2s_cfg_t datacfg = {.port = I2S_NUM_0, .rx_handle = rx, .tx_handle = tx};
    const audio_codec_data_if_t *data = audio_codec_new_i2s_data(&datacfg);
    const audio_codec_gpio_if_t *gpio = audio_codec_new_gpio();
    ESP_RETURN_ON_FALSE(data && gpio, ESP_ERR_NO_MEM, TAG, "Codec interfaces");
    audio_codec_i2c_cfg_t ctrlcfg = {
        .port = I2C_NUM_0, .addr = ES7210_CODEC_DEFAULT_ADDR, .bus_handle = clik_i2c};
    const audio_codec_ctrl_if_t *micctrl = audio_codec_new_i2c_ctrl(&ctrlcfg);
    ESP_RETURN_ON_FALSE(micctrl, ESP_ERR_NO_MEM, TAG, "Mic control");
    es7210_codec_cfg_t mc = {.ctrl_if = micctrl, .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2};
    const audio_codec_if_t *miccodec = es7210_codec_new(&mc);
    ESP_RETURN_ON_FALSE(miccodec, ESP_FAIL, TAG, "ES7210");
    const audio_codec_if_t *outcodec;
#if CONFIG_CLIK_BOARD_CORES3
    ctrlcfg.addr = AW88298_CODEC_DEFAULT_ADDR;
    const audio_codec_ctrl_if_t *outctrl = audio_codec_new_i2c_ctrl(&ctrlcfg);
    aw88298_codec_cfg_t oc = {.ctrl_if = outctrl, .gpio_if = gpio, .hw_gain.pa_gain = 15};
    outcodec = aw88298_codec_new(&oc);
#else
    ctrlcfg.addr = ES8311_CODEC_DEFAULT_ADDR;
    const audio_codec_ctrl_if_t *outctrl = audio_codec_new_i2c_ctrl(&ctrlcfg);
    es8311_codec_cfg_t oc = {.ctrl_if = outctrl,
                             .gpio_if = gpio,
                             .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
                             .pa_pin = 7,
                             .use_mclk = true,
                             .hw_gain = {.pa_voltage = 5.0, .codec_dac_voltage = 3.3}};
    outcodec = es8311_codec_new(&oc);
#endif
    ESP_RETURN_ON_FALSE(outctrl && outcodec, ESP_FAIL, TAG, "Speaker codec");
    esp_codec_dev_cfg_t dc = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN, .codec_if = miccodec, .data_if = data};
    mic = esp_codec_dev_new(&dc);
    dc.dev_type = ESP_CODEC_DEV_TYPE_OUT;
    dc.codec_if = outcodec;
    speaker = esp_codec_dev_new(&dc);
    ESP_RETURN_ON_FALSE(mic && speaker, ESP_ERR_NO_MEM, TAG, "Codec devices");
    esp_codec_dev_sample_info_t info = {.sample_rate = 16000, .channel = 2, .bits_per_sample = 16};
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(mic, &info) == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "Mic open");
    ESP_RETURN_ON_FALSE(esp_codec_dev_open(speaker, &info) == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "Speaker open");
    esp_codec_dev_set_in_gain(mic, 24.0f);
    esp_codec_dev_set_out_vol(speaker, 55);
    return ESP_OK;
}
esp_err_t clik_board_record(int16_t *out, size_t count) {
    if (!mic || !out)
        return ESP_ERR_INVALID_STATE;
    if (xSemaphoreTake(audio_mutex, pdMS_TO_TICKS(1000)) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    esp_err_t ret = ESP_OK;
    int16_t frame[512];
    for (size_t pos = 0; pos < count;) {
        size_t n = count - pos > 256 ? 256 : count - pos;
        if (esp_codec_dev_read(mic, frame, n * 4) != ESP_CODEC_DEV_OK) {
            ret = ESP_FAIL;
            break;
        }
        /* Two microphones are acquired. Keep channel 1 for STT; do not claim
         * acoustic beamforming/AEC from a simple average of uncalibrated mics. */
        for (size_t i = 0; i < n; i++)
            out[pos + i] = frame[i * 2];
        pos += n;
    }
    xSemaphoreGive(audio_mutex);
    return ret;
}
esp_err_t clik_board_play(const int16_t *in, size_t count) {
    if (!speaker || !in)
        return ESP_ERR_INVALID_STATE;
    if (xSemaphoreTake(audio_mutex, pdMS_TO_TICKS(1000)) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    esp_err_t ret = ESP_OK;
    int16_t frame[512];
    for (size_t pos = 0; pos < count;) {
        size_t n = count - pos > 256 ? 256 : count - pos;
        for (size_t i = 0; i < n; i++)
            frame[i * 2] = frame[i * 2 + 1] = in[pos + i];
        if (esp_codec_dev_write(speaker, frame, n * 4) != ESP_CODEC_DEV_OK) {
            ret = ESP_FAIL;
            break;
        }
        pos += n;
    }
    xSemaphoreGive(audio_mutex);
    return ret;
}
