# Meeting Room URL Creation Feature - Implementation Guide

## Overview
A complete meeting room URL creation system has been added to your LiveKit meeting application. Users can now:
1. **Create unique meeting rooms** with shareable URLs
2. **Join existing rooms** using room names
3. **Share meeting links** that automatically pre-fill room information
4. **Support multiple rooms with the same name** using unique room IDs

---

## Features Implementation

### 1. Backend Changes (`app.py`)

#### New Endpoint: `/create-room` (POST)
Creates a new meeting room with a unique ID and generates a shareable URL.

**Request:**
```json
{
  "room_name": "team-sync"
}
```

**Response:**
```json
{
  "room_id": "550e8400-e29b-41d4-a716-446655440000",
  "room_name": "team-sync",
  "meeting_url": "http://localhost:8080/?room=team-sync&id=550e8400-e29b-41d4-a716-446655440000"
}
```

#### Updated `/token` Endpoint
Now accepts an optional `room_id` parameter to track which specific room instance a user is joining:

**Request:**
```json
{
  "room_name": "team-sync",
  "participant_name": "Alice",
  "room_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

#### New Models
- `RoomCreateRequest`: Validates room name for creation
- `RoomCreateResponse`: Returns room details and shareable URL

---

### 2. Frontend Changes (`index.html`)

#### Lobby Mode Tabs
Two tabs in the join lobby:
- **Join Room**: Traditional join with room name + participant name
- **Create Room**: Generate unique room URLs

#### Create Room Flow
1. User enters a room name
2. Clicks "Generate Link" button
3. Backend creates unique room ID
4. Displays:
   - Room name and unique ID
   - Full shareable URL (copyable)
   - Option to join immediately

#### Join Room Flow (Enhanced)
1. Traditional entry form
2. **NEW**: Automatically pre-fills when accessed via shared URL (e.g., `?room=team-sync&id=<uuid>`)
3. User enters name and joins

#### URL Parameter Support
```
http://localhost:8080/?room=team-sync&id=550e8400-e29b-41d4-a716-446655440000
```

When users access via this URL:
- Room name is automatically populated
- Focus moves to name input field
- User only needs to enter their name to join

---

## Use Cases

### Use Case 1: Ad-hoc Meeting
1. User clicks "Create Room" tab
2. Enters room name: "design-review"
3. Clicks "Generate Link"
4. Copies link and shares with team: `http://localhost:8080/?room=design-review&id=xxx`
5. All participants who use this link join the SAME instance (same unique ID)

### Use Case 2: Multiple Parallel Meetings
1. Meeting 1: Room name "design-review", ID: `uuid-001`
   - URL: `/?room=design-review&id=uuid-001`
2. Meeting 2: Room name "design-review", ID: `uuid-002`
   - URL: `/?room=design-review&id=uuid-002`
3. **Different unique IDs** = **Separate independent rooms** with the same name

### Use Case 3: Traditional Join
1. User visits application
2. Enters room name and participant name manually
3. Joins room (works as before)

---

## Technical Details

### Unique Room ID Generation
- Uses UUID v4 format: `550e8400-e29b-41d4-a716-446655440000`
- Generated server-side for uniqueness guarantee
- Prevents room ID collision

### URL Construction
The meeting URL is constructed by the server:
```python
meeting_url = f"{protocol}://{host}/?room={room_name}&id={room_id}"
```

Handles:
- Protocol detection (http/https via `x-forwarded-proto`)
- Host detection (respects reverse proxies)
- URL encoding for special characters

### URL Parameter Handling
On page load:
1. JavaScript reads URL parameters: `?room=X&id=Y`
2. If `room` parameter exists, auto-fills room name
3. Switches to "Join Room" tab
4. Focuses on participant name input

---

## Security Considerations

1. **Room Name Validation**
   - Alphanumeric, hyphens, underscores, dots, spaces only
   - Max 128 characters
   - Prevents injection attacks

2. **URL Security**
   - Room ID is standard UUID (non-sequential, hard to guess)
   - No authentication required (as per original design)
   - Access control via LiveKit server

3. **Privacy**
   - URLs are not stored on server
   - Each generated URL is independent
   - No tracking or logging of shared links

---

## Testing the Feature

### Test 1: Create Room
```
URL: http://localhost:8080
Steps:
1. Click "Create Room" tab
2. Enter: "test-meeting"
3. Click "Generate Link"
4. Observe: Room created with ID displayed
5. Copy link
```

### Test 2: Join Created Room (Multi-User)
```
Browser 1: Click "Join This Room" after creation
Browser 2: Paste the copied link in address bar
Result: Both join the SAME room instance
```

### Test 3: Multiple Rooms with Same Name
```
Browser 1: Create room "team-sync" → ID: uuid-001
Browser 2: Create room "team-sync" → ID: uuid-002
Result: Two fully separate rooms despite having same name
```

### Test 4: URL Auto-Fill
```
Link: http://localhost:8080/?room=design-review&id=xxx
Steps:
1. Open link in browser
2. Observe: Room name "design-review" pre-filled
3. Enter participant name
4. Join successfully
```

---

## File Changes Summary

| File | Changes |
|------|---------|
| `app.py` | Added `/create-room` endpoint, updated models, enhanced `/token` endpoint |
| `index.html` | Added Create Room UI, mode tabs, URL parameter handling, new functions |

---

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/create-room` | Generate unique room with shareable URL |
| POST | `/token` | Get JWT token (now with optional room_id) |
| GET | `/` | Serve frontend (supports URL parameters) |
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check |

---

## Future Enhancements (Optional)

1. **Room Persistence**: Store created rooms in database
2. **Expiring Links**: Set URL expiration time
3. **Room Analytics**: Track room creation, joins, duration
4. **Custom Room IDs**: Allow users to set custom identifiers
5. **Room Password**: Add optional protection for rooms
6. **Room Permissions**: Control who can create/join
7. **Mobile QR Code**: Generate QR codes for easy sharing

---

## Troubleshooting

### Issue: URL parameters not working
**Solution**: Ensure page is accessed with `?room=name&id=uuid` format

### Issue: Copy button not working
**Solution**: Check browser permissions for clipboard access

### Issue: Room names with spaces
**Solution**: Already supported - spaces are allowed and preserved

### Issue: Error "Failed to create room"
**Solution**: 
- Check server logs for validation errors
- Ensure room name meets requirements (1-128 chars)
- Verify LIVEKIT credentials in .env
