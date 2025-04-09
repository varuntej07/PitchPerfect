import functools
import asyncio


def retry(max_attempts, delay):
    """ A decorator that retries an asynchronous function up to max_attempts times,
        waiting for 'delay' seconds between attempts """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    attempts += 1
                    print(f"[Error] in {func.__name__} : {e}. Re-trying {attempts}/{max_attempts}...")
                    if attempts < max_attempts:
                        await asyncio.sleep(delay)
                    else:
                        print(f"All {max_attempts} attempts failed for {func.__name__}")
            raise Exception(f"Max retry attempts reached for {func.__name__}")

        return wrapper

    return decorator
