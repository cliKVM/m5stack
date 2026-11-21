/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "sdkconfig.h"
#include "driver/i2c_master.h"
extern i2c_master_bus_handle_t clik_i2c;
esp_err_t clik_board_audio_power(void);
#if CONFIG_CLIK_BOARD_CORES3
#define CLIK_I2C_SDA 12
#define CLIK_I2C_SCL 11
#define CLIK_I2S_MCLK 0
#define CLIK_I2S_BCLK 34
#define CLIK_I2S_WS 33
#define CLIK_I2S_DOUT 13
#define CLIK_I2S_DIN 14
#else
#define CLIK_I2C_SDA 42
#define CLIK_I2C_SCL 41
#define CLIK_I2S_MCLK 8
#define CLIK_I2S_BCLK 9
#define CLIK_I2S_WS 10
#define CLIK_I2S_DOUT 12
#define CLIK_I2S_DIN 11
#endif
