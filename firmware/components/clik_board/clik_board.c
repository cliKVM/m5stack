/* SPDX-License-Identifier: Apache-2.0
 * Board pin/codec/display setup based on the official Espressif CoreS3 BSP
 * and Waveshare LCD-1.54 examples. See NOTICE and docs/hardware.md.
 */
#include "clik_board.h"
#include "board_private.h"
#include <ctype.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_ili9341.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "font5x7.h"
#include "ili9342e_init_cmds.h"

static const char *TAG = "clik_board";
i2c_master_bus_handle_t clik_i2c;
static i2c_master_dev_handle_t touch;
#if CONFIG_CLIK_BOARD_CORES3
static i2c_master_dev_handle_t expander, pmu;
#endif
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t draw_mutex, draw_done;
static uint16_t *stripe;
static int width = 240;
static bool display_ready;

#if CONFIG_CLIK_BOARD_CORES3 || CONFIG_CLIK_LCD154_TOUCH
static esp_err_t add_i2c(uint8_t addr, i2c_master_dev_handle_t *dev) {
    const i2c_device_config_t c = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = addr, .scl_speed_hz = 100000};
    return i2c_master_bus_add_device(clik_i2c, &c, dev);
}
#endif
#if CONFIG_CLIK_BOARD_CORES3
static esp_err_t read_reg(i2c_master_dev_handle_t d, uint8_t reg, uint8_t *v) {
    return i2c_master_transmit_receive(d, &reg, 1, v, 1, 100);
}
static esp_err_t write_reg(i2c_master_dev_handle_t d, uint8_t reg, uint8_t v) {
    uint8_t b[2] = {reg, v};
    return i2c_master_transmit(d, b, 2, 100);
}
static esp_err_t update_reg(i2c_master_dev_handle_t d, uint8_t reg, uint8_t mask, bool on) {
    uint8_t v;
    ESP_RETURN_ON_ERROR(read_reg(d, reg, &v), TAG, "I2C read");
    return write_reg(d, reg, on ? (v | mask) : (v & ~mask));
}
static esp_err_t exp_pin(unsigned pin, bool high) {
    unsigned port = pin / 8;
    uint8_t mask = 1u << (pin % 8);
    ESP_RETURN_ON_ERROR(update_reg(expander, 0x12 + port, mask, true), TAG, "AW9523 GPIO mode");
    ESP_RETURN_ON_ERROR(update_reg(expander, 0x02 + port, mask, high), TAG, "AW9523 latch");
    return update_reg(expander, 0x04 + port, mask, false);
}
#endif
esp_err_t clik_board_host_power(bool enable) {
#if CONFIG_CLIK_BOARD_CORES3
    /* This function is called only after local physical Start. Output source
     * is selected at build time; never infer a safe cable from USB software. */
    if (!enable)
        return exp_pin(5, false);
#if CONFIG_CLIK_CORES3_BATTERY_POWER
    uint8_t state;
    ESP_RETURN_ON_ERROR(read_reg(pmu, 0x00, &state), TAG, "AXP state");
    ESP_RETURN_ON_FALSE(state & 0x08, ESP_ERR_INVALID_STATE, TAG, "No battery detected");
    ESP_RETURN_ON_ERROR(exp_pin(15, true), TAG, "5V boost");
    /* Ramp BUS_OUT to limit inrush, following M5Unified's power sequence. */
    for (unsigned i = 0; i < 24; i++) {
        ESP_RETURN_ON_ERROR(exp_pin(1, true), TAG, "BUS precharge on");
        esp_rom_delay_us(i * 16);
        ESP_RETURN_ON_ERROR(exp_pin(1, false), TAG, "BUS precharge off");
        esp_rom_delay_us(50);
    }
    ESP_RETURN_ON_ERROR(exp_pin(1, true), TAG, "BUS battery output");
#else
    /* External regulated BUS 5V (DINBase): retain BUS as input. */
    ESP_RETURN_ON_ERROR(exp_pin(15, false), TAG, "Boost off for external BUS");
    vTaskDelay(pdMS_TO_TICKS(200));
    ESP_RETURN_ON_ERROR(exp_pin(1, false), TAG, "BUS input");
#endif
    ESP_RETURN_ON_ERROR(exp_pin(5, true), TAG, "USB VBUS output");
    uint8_t verify;
    ESP_RETURN_ON_ERROR(read_reg(expander, 0x02, &verify), TAG, "USB output verify");
    return (verify & 0x20) ? ESP_OK : ESP_FAIL;
#else
    /* Waveshare has no host 5V source. The external powered adapter is part
     * of the hardware design, documented separately. No GPIO can create it. */
    (void)enable;
    return ESP_OK;
#endif
}

esp_err_t clik_board_audio_power(void) {
#if CONFIG_CLIK_BOARD_CORES3
    ESP_RETURN_ON_ERROR(write_reg(pmu, 0x92, 13), TAG, "AW88298 1.8V");
    ESP_RETURN_ON_ERROR(write_reg(pmu, 0x93, 28), TAG, "ES7210 3.3V");
    ESP_RETURN_ON_ERROR(update_reg(pmu, 0x90, 0x03, true), TAG, "Audio LDO enable");
    return exp_pin(2, true);
#else
    gpio_config_t g = {.pin_bit_mask = 1ULL << 7, .mode = GPIO_MODE_OUTPUT};
    ESP_RETURN_ON_ERROR(gpio_config(&g), TAG, "PA GPIO");
    return gpio_set_level(7, 1);
#endif
}

static bool draw_cb(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *event, void *ctx) {
    (void)io;
    (void)event;
    (void)ctx;
    BaseType_t wake = pdFALSE;
    xSemaphoreGiveFromISR(draw_done, &wake);
    return wake == pdTRUE;
}
static bool flush_stripe(int y, int h) {
    if (esp_lcd_panel_draw_bitmap(panel, 0, y, width, y + h, stripe) != ESP_OK)
        return false;
    return xSemaphoreTake(draw_done, pdMS_TO_TICKS(1000)) == pdTRUE;
}
static void fill(uint16_t color) {
    for (int i = 0; i < width * 16; i++)
        stripe[i] = __builtin_bswap16(color);
    for (int y = 0; y < 240; y += 16)
        if (!flush_stripe(y, 16))
            break;
}
static void line(int y, const char *text, uint16_t color) {
    const int scale = 2, step = 12;
    for (int i = 0; i < width * 16; i++)
        stripe[i] = __builtin_bswap16(0x1082);
    if (text)
        for (size_t k = 0; text[k] && k < (size_t)((width - 16) / step); k++) {
            unsigned ch = (unsigned char)toupper((unsigned char)text[k]);
            if (ch > 127)
                ch = '?';
            for (int yy = 0; yy < 7; yy++)
                for (int xx = 0; xx < 5; xx++) {
                    if (!(clik_font[ch][yy] & (1u << (4 - xx))))
                        continue;
                    for (int dy = 0; dy < scale; dy++)
                        for (int dx = 0; dx < scale; dx++)
                            stripe[(yy * scale + dy) * width + 8 + (int)k * step + xx * scale +
                                   dx] = __builtin_bswap16(color);
                }
        }
    flush_stripe(y, 16);
}
void clik_board_status(const char *title, const char *a, const char *b, const char *c) {
    if (!display_ready || xSemaphoreTake(draw_mutex, pdMS_TO_TICKS(2000)) != pdTRUE)
        return;
    fill(0x1082);
    line(12, "HEY ASA / CLIKVM", 0xFAE6);
    line(48, title, 0xFFFF);
    line(90, a, 0xFFFF);
    line(122, b, 0xD69A);
    line(154, c, 0xD69A);
    line(210, "USB + VOICE", 0xFAE6);
    xSemaphoreGive(draw_mutex);
}
const char *clik_board_name(void) {
#if CONFIG_CLIK_BOARD_CORES3
    return "cores3";
#elif CONFIG_CLIK_LCD154_TOUCH
    return "lcd154-touch";
#else
    return "lcd154";
#endif
}
clik_board_input_t clik_board_input(void) {
    clik_board_input_t r = {0};
    if (touch) {
        uint8_t reg = 0x02, data[5];
        if (i2c_master_transmit_receive(touch, &reg, 1, data, sizeof data, 30) == ESP_OK &&
            (data[0] & 15)) {
            r.pressed = true;
            r.x = ((data[1] & 15) << 8) | data[2];
            r.y = ((data[3] & 15) << 8) | data[4];
        }
    }
#if CONFIG_CLIK_BOARD_LCD154
    if (gpio_get_level(0) == 0)
        r.pressed = true;
#endif
    return r;
}
esp_err_t clik_board_init(void) {
    draw_mutex = xSemaphoreCreateMutex();
    draw_done = xSemaphoreCreateBinary();
    ESP_RETURN_ON_FALSE(draw_mutex && draw_done, ESP_ERR_NO_MEM, TAG, "Display semaphores");
    i2c_master_bus_config_t i2c = {.i2c_port = I2C_NUM_0,
                                   .sda_io_num = CLIK_I2C_SDA,
                                   .scl_io_num = CLIK_I2C_SCL,
                                   .clk_source = I2C_CLK_SRC_DEFAULT,
                                   .glitch_ignore_cnt = 7,
                                   .flags.enable_internal_pullup = true};
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c, &clik_i2c), TAG, "I2C bus");
    int dc, cs, sclk, mosi, rst;
#if CONFIG_CLIK_BOARD_CORES3
    width = 320;
    dc = 35;
    cs = 3;
    sclk = 36;
    mosi = 37;
    rst = -1;
    ESP_RETURN_ON_ERROR(add_i2c(0x58, &expander), TAG, "AW9523");
    ESP_RETURN_ON_ERROR(add_i2c(0x34, &pmu), TAG, "AXP2101");
    ESP_RETURN_ON_ERROR(clik_board_host_power(false), TAG, "USB power off");
    ESP_RETURN_ON_ERROR(update_reg(expander, 0x11, 0x10, true), TAG, "Push pull");
    ESP_RETURN_ON_ERROR(exp_pin(9, true), TAG, "LCD reset");
    ESP_RETURN_ON_ERROR(exp_pin(0, true), TAG, "Touch reset");
    ESP_RETURN_ON_ERROR(write_reg(pmu, 0x99, 24), TAG, "Backlight voltage");
    ESP_RETURN_ON_ERROR(update_reg(pmu, 0x90, 0x80, true), TAG, "Backlight on");
    ESP_RETURN_ON_ERROR(add_i2c(0x38, &touch), TAG, "Touch");
    vTaskDelay(pdMS_TO_TICKS(300));
#else
    dc = 45;
    cs = 21;
    sclk = 38;
    mosi = 39;
    rst = 40;
    gpio_config_t g = {.pin_bit_mask = 1ULL << 46, .mode = GPIO_MODE_OUTPUT};
    ESP_RETURN_ON_ERROR(gpio_config(&g), TAG, "Backlight GPIO");
    gpio_set_level(46, 1);
    g.pin_bit_mask = 1ULL << 0;
    g.mode = GPIO_MODE_INPUT;
    g.pull_up_en = true;
    ESP_RETURN_ON_ERROR(gpio_config(&g), TAG, "BOOT button");
#if CONFIG_CLIK_LCD154_TOUCH
    ESP_RETURN_ON_ERROR(add_i2c(0x15, &touch), TAG, "CST816");
#endif
#endif
    spi_bus_config_t bus = {.sclk_io_num = sclk,
                            .mosi_io_num = mosi,
                            .miso_io_num = -1,
                            .quadwp_io_num = -1,
                            .quadhd_io_num = -1,
                            .max_transfer_sz = width * 16 * 2};
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO), TAG, "Display SPI");
    esp_lcd_panel_io_spi_config_t io_cfg = {.dc_gpio_num = dc,
                                            .cs_gpio_num = cs,
                                            .pclk_hz = 40000000,
                                            .lcd_cmd_bits = 8,
                                            .lcd_param_bits = 8,
                                            .spi_mode = 0,
                                            .trans_queue_depth = 1,
                                            .on_color_trans_done = draw_cb};
    esp_lcd_panel_io_handle_t io;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI3_HOST, &io_cfg, &io),
                        TAG, "LCD IO");
    esp_lcd_panel_dev_config_t cfg = {
        .reset_gpio_num = rst, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR, .bits_per_pixel = 16};
#if CONFIG_CLIK_BOARD_CORES3
    uint8_t firmware_id = 0;
    ili9341_vendor_config_t vendor = {.init_cmds = ili9342e_init_cmds,
                                      .init_cmds_size =
                                          sizeof ili9342e_init_cmds / sizeof ili9342e_init_cmds[0]};
    if (read_reg(touch, 0xA6, &firmware_id) == ESP_OK && firmware_id == 0x12)
        cfg.vendor_config = &vendor;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_ili9341(io, &cfg, &panel), TAG, "ILI9342");
#else
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io, &cfg, &panel), TAG, "ST7789");
#endif
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(panel), TAG, "Panel reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "Panel init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(panel, true), TAG, "Panel inversion");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel, true), TAG, "Panel on");
    stripe = heap_caps_malloc(width * 16 * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    ESP_RETURN_ON_FALSE(stripe, ESP_ERR_NO_MEM, TAG, "Display stripe buffer");
    display_ready = true;
    clik_board_status("STARTING", clik_board_name(), "USB HOST OFF", "");
    return ESP_OK;
}
