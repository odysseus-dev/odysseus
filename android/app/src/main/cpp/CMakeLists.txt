cmake_minimum_required(VERSION 3.22.1)

project(odysseus_native_loader C)

add_library(odysseus_native_loader SHARED odysseus_native_loader.c)

target_link_libraries(odysseus_native_loader dl)
