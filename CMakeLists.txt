cmake_minimum_required(VERSION 3.16)

project(Win32Calculator LANGUAGES CXX)

add_executable(Win32Calculator WIN32
    src/main.cpp
)

target_compile_features(Win32Calculator PRIVATE cxx_std_17)

if(MSVC)
    target_compile_options(Win32Calculator PRIVATE /W4 /permissive- /GS- /GR- /EHs-c-)
    target_link_options(Win32Calculator PRIVATE /NODEFAULTLIB /ENTRY:wWinMainCRTStartup)
    target_link_libraries(Win32Calculator PRIVATE kernel32 user32 gdi32)
else()
    target_compile_options(Win32Calculator PRIVATE -Wall -Wextra -pedantic)
endif()
