set(TARGET whisper-stream-mq)
add_executable(${TARGET} stream-mq.cpp)

include(DefaultTargetOptions)

target_link_libraries(${TARGET} PRIVATE whisper ${CMAKE_THREAD_LIBS_INIT})

install(TARGETS ${TARGET} RUNTIME)

