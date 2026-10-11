import hashlib

from ...core.config_manager import Config


def sample_ranges(file_size):
    head = max(0, Config.HASH_HEAD_BYTES)
    middle = max(0, Config.HASH_MIDDLE_BYTES)
    tail = max(0, Config.HASH_TAIL_BYTES)

    if file_size <= 0:
        return []

    if file_size <= head + middle + tail:
        return [(0, file_size)]

    ranges = []
    if head:
        ranges.append((0, head))

    if middle:
        tail_start = file_size - tail
        start = (file_size - middle) // 2
        if start < head:
            start = head
        if start + middle > tail_start:
            start = tail_start - middle
        if start >= head and start + middle <= tail_start:
            ranges.append((start, start + middle))

    if tail:
        ranges.append((file_size - tail, file_size))

    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


async def _read_range(client, message, start, end, channel_id, message_id):
    chunk_size = Config.HASH_CHUNK_SIZE
    first_chunk = start // chunk_size
    last_chunk = (end + chunk_size - 1) // chunk_size
    offset = first_chunk
    limit = last_chunk - first_chunk

    current_message = message
    for attempt in range(3):
        buffer = bytearray()
        position = first_chunk * chunk_size
        try:
            async for data in client.stream_media(
                current_message, limit=limit, offset=offset
            ):
                segment_start = position
                segment_end = position + len(data)
                position = segment_end
                low = start if start > segment_start else segment_start
                high = end if end < segment_end else segment_end
                if low < high:
                    buffer += data[low - segment_start : high - segment_start]
            return bytes(buffer)
        except Exception as error:
            if not _is_file_reference_expired(error) or attempt == 2:
                raise
            current_message = await client.get_messages(
                channel_id, message_id, replies=0
            )
            if isinstance(current_message, (list, tuple)):
                current_message = current_message[0] if current_message else None
            if current_message is None or getattr(current_message, "empty", False):
                raise


def _is_file_reference_expired(error):
    return error.__class__.__name__ == "FileReferenceExpired"


async def compute_md5(pool, message, file_size, channel_id, message_id):
    digest = hashlib.md5()
    for start, end in sample_ranges(file_size):
        data = await pool.call(
            lambda client, s=start, e=end: _read_range(
                client, message, s, e, channel_id, message_id
            ),
            timeout=Config.REQUEST_TIMEOUT,
        )
        digest.update(data)
    digest.update(str(file_size).encode())
    return digest.hexdigest()
