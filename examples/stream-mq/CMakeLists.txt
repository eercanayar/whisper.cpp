# # Add ZMQ
# if(NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/third_party/libzmq/CMakeLists.txt)
#     execute_process(
#         COMMAND git clone https://github.com/zeromq/libzmq.git third_party/libzmq
#         WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
#     )
# endif()

# if(NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/third_party/cppzmq/zmq.hpp)
#     execute_process(
#         COMMAND git clone https://github.com/zeromq/cppzmq.git third_party/cppzmq
#         WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
#     )
# endif()

# Build ZMQ from source
set(ZMQ_BUILD_TESTS OFF CACHE BOOL "Disable ZMQ tests")
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/third_party/libzmq)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/third_party/cppzmq)

# Your existing target
set(TARGET whisper-stream-mq)
add_executable(${TARGET} stream-mq.cpp)
include(DefaultTargetOptions)
target_link_libraries(${TARGET} PRIVATE 
    common
    whisper 
    libzmq-static
    ${CMAKE_THREAD_LIBS_INIT}
)
install(TARGETS ${TARGET} RUNTIME)