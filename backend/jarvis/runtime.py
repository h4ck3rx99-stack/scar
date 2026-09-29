"""Process-wide handles set up by __main__ (TTS, session, memory, file index...), so tools can reach them
without threading every object through every constructor. All optional: tests leave them as None."""
tts = None          # KokoroTTS
session = None      # core.session.Session
memory = None       # skills.memory.MemoryDB
files = None        # skills.docs.FileIndex
voiceprint = None   # audio.speaker.VoicePrint
phone = None        # core.phone.PhoneRemote
