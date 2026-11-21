if(CONFIG_CLIK_MODE_USB_HID)
  set(CLIK_HID_SOURCES "clik_hid.c")
else()
  set(CLIK_HID_SOURCES "clik_hid_stub.c")
endif()
idf_component_register(SRCS ${CLIK_HID_SOURCES} INCLUDE_DIRS "include"
  REQUIRES esp_tinyusb esp_timer esp_hw_support)
