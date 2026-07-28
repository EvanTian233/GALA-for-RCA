import logging


def setup_logging(log_file_path, log_name='my_logger'):
    # Create a logger instance
    logger = logging.getLogger(log_name)
    logger.setLevel(logging.DEBUG)

    # Create file handler for writing logs to file
    file_handler = logging.FileHandler(log_file_path)
    file_handler.setLevel(logging.DEBUG)

    # Create console handler for outputting logs to console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)

    # Create formatter to define log format
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


if __name__ == "__main__":
    log_file_path = "example.log"

    # Setup logging
    logger = setup_logging(log_file_path)

    # Log some messages
    logger.debug("This is a debug message.")
    logger.info("This is an info message.")
    logger.warning("This is a warning message.")
    logger.error("This is an error message.")
    logger.critical("This is a critical message.")
