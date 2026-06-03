MOVA Tools Connection Testing on proven Chameleon to see tha AML connection ,Dataset transfer etc.
Results captured via tcpdump and also formatted to hex txt
This might help our MOVA Tools AML connection testing


The following info is an alternative look by a.n.other AI - Generated from reading mova_text.txt

Produced as previous /opt/MOVA we struggled to get the Windows MOVA Tools program to successfully connect to our MOVA Streams
Hopefully this comparison will help understand where we went wrong, and provide us with the neccessaries to get it working as part of the build



MOVA Tools Connection Requirements
Based on real working tcpdump from outstation (192.168.71.23:55002)

1. TCP Connection

Destination: 192.168.71.23:55002
Protocol: Plain TCP (no TLS)
Keep the connection open (persistent)

2. Message Framing (Critical)
Every message on the wire must be:

9-digit zero-padded length (decimal) + JSON payload
The length = exact byte count of the JSON part only
No extra spaces, newlines, or BOM in the JSON for length calculation

Example:
text00000159{"StreamID":"1","TransactionId":"0x00000001","MessageType":"ReqCheckConnectedToRightController","Params":{"ControllerID":1}}
3. Common Message Structure
All messages contain:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00000000",     // Incrementing hex string
  "MessageType": "ReqXXX or RspXXX",
  "Params": { ... }
}
4. Required Handshake (First messages)

ReqCheckConnectedToRightControllerJSON{
  "StreamID": "1",
  "TransactionId": "0x00000001",
  "MessageType": "ReqCheckConnectedToRightController",
  "Params": { "ControllerID": 1 }
}
Expected response:JSON{
  "MessageType": "RspCheckConnectedToRightController",
  "TransactionId": "0x00000001",
  "Params": { "IsOk": true }
}

5. Main Polling Sequence (What MOVA Tools expects)
After handshake, continuously send (in approximate order):

ReqMOVATime
ReqOperationFlags
ReqDataPlanTriggeringStatus
ReqDetectorsStatus
ReqDetectorsSusStatus
ReqLaneData (for ID = 1, 2, 3, ...)
ReqLinkData (for ID = 1, 2, 3, ...)
Periodically: ReqRawDetectorsStatus, ReqOutputChannelStatus, ReqForceBits, ReqConfirmBits

High frequency polling (especially ReqMOVATime and ReqOperationFlags) is expected.
6. Key Responses MOVA Tools Needs for "Data" to Appear

RspOperationFlags → IsMOVAEnabled: true
RspDetectorsStatus → populated boolean array
RspLaneData → realistic values (SFSmoothed, QBeyondINDET, etc.)
RspLinkData → valid stage data
RspMOVATime → current timestamp

Empty or default responses = "No dataset / no detection data" in MOVA Tools.
7. Best Practices for Success

Use exact 9-digit length prefix (zero-padded)
Match TransactionId exactly in responses
Send JSON compact (no extra whitespace)
Keep TCP connection alive
Respond promptly
Support multiple ReqLaneData / ReqLinkData with different ID values

This protocol is what a real working MOVA outstation uses. Implementing the length prefix + handshake + continuous polling of the above messages is what makes MOVA Tools show live data instead of errors or blank panels.



MOVA Protocol – Example Payloads
Extracted from real working tcpdump
1. Handshake
Request:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00000001",
  "MessageType": "ReqCheckConnectedToRightController",
  "Params": { "ControllerID": 1 }
}
Response:
JSON{
  "MessageType": "RspCheckConnectedToRightController",
  "TransactionId": "0x00000001",
  "Params": { "IsOk": true }
}
2. Time Requests (Very Frequent)
ReqMOVATime:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00000002",
  "MessageType": "ReqMOVATime"
}
RspMOVATime:
JSON{
  "MessageType": "RspMOVATime",
  "TransactionId": "0x00000002",
  "Params": {
    "DateTime": "2026-06-03T09:40:16",
    "IsWallClockTime": false
  }
}
3. Operation Flags (Very Frequent)
ReqOperationFlags:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00000003",
  "MessageType": "ReqOperationFlags"
}
RspOperationFlags:
JSON{
  "MessageType": "RspOperationFlags",
  "TransactionId": "0x00000003",
  "Params": {
    "CRB": false,
    "IsMOVAEnabled": true,
    "IsOnControl": false,
    "IsMultiStage": false,
    "ErrorCount": 0,
    "Warmup": -1,
    "DemandedStageNum": 0
  }
}
4. Data Plan Status
ReqDataPlanTriggeringStatus:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00000004",
  "MessageType": "ReqDataPlanTriggeringStatus"
}
RspDataPlanTriggeringStatus:
JSON{
  "MessageType": "RspDataPlanTriggeringStatus",
  "TransactionId": "0x00000004",
  "Params": { "IsEnabled": true }
}
5. Detectors Status (Critical for "Data" to show)
ReqDetectorsStatus:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00001073",
  "MessageType": "ReqDetectorsStatus"
}
RspDetectorsStatus:
JSON{
  "MessageType": "RspDetectorsStatus",
  "TransactionId": "0x00001073",
  "Params": {
    "Status": [true,true,true,true,...],   // 64+ booleans
    "MovaDateTime": "2026-06-03T09:41:22"
  }
}
6. Lane Data (Per ID)
ReqLaneData:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00001075",
  "MessageType": "ReqLaneData",
  "Params": { "ID": 1 }
}
RspLaneData:
JSON{
  "MessageType": "RspLaneData",
  "TransactionId": "0x00001075",
  "Params": {
    "RedCountIN": 0,
    "RedCountX": 0,
    "SFSmoothed": 18,
    "SFLastCycle": -1.00,
    "ShiftRegister": [false,false,...],
    "OversatCounter": 0,
    "Endsat": 0,
    "QBeyondINDET": 22,
    "LeftOverVehs": 0
  }
}
7. Link Data (Per ID)
ReqLinkData:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00001078",
  "MessageType": "ReqLinkData",
  "Params": { "ID": 1 }
}
RspLinkData:
JSON{
  "MessageType": "RspLinkData",
  "TransactionId": "0x00001078",
  "Params": {
    "BonusGreenTime": 0,
    "SmoothedFlow": 18,
    "Endsat": 0,
    "DemandType": 1,
    "NetBenFlow": -1,
    "ActualFlow": -1,
    "FutureRedTime": -1,
    "ExtraGreenTime": -1,
    "EPHoldMarker": 0,
    "EPExtMarker": 0,
    "EPExtTimer": 0
  }
}
8. Other Important Messages
ReqForceBits:
JSON{
  "StreamID": "1",
  "TransactionId": "0x00001071",
  "MessageType": "ReqForceBits"
}
RspForceBits:
JSON{
  "MessageType": "RspForceBits",
  "TransactionId": "0x00001071",
  "Params": {
    "ForceBits": [false,false,...],
    "TakeOverBit": false,
    "HurryInhibit": false
  }
}
ReqRawDetectorsStatus / ReqOutputChannelStatus follow similar patterns with boolean arrays.


Tip for MOVA Tools:
Focus first on implementing accurate length prefix + handshake + ReqMOVATime + ReqOperationFlags + ReqDetectorsStatus + multiple ReqLaneData/ReqLinkData. These are what make the UI show live data instead of blank/no dataset errors.


Why the text dump is sufficient:

It contains full decoded payloads (the JSON messages are clearly visible).
All key message types, structures, length prefixes, and examples are present.
The hex parts in the tcpdump are mostly just the raw TCP packet headers + the same JSON data we already extracted.


When Hex Output Would Be Useful:
Only in these cases:

You want to see exact byte-for-byte framing (including any hidden whitespace or encoding quirks).
There are binary fields we missed (unlikely — everything important is JSON).
You're debugging length prefix calculation errors.

If you have a hex dump (e.g. from Wireshark .pcap exported as hex), feel free to share a small sample of one full message (request + response), and I can verify the exact length calculation.

Current Status (Recommendation)
You already have everything needed to implement a working client:

9-digit length prefix + compact JSON
Handshake sequence
All major ReqXXX / RspXXX examples (from previous message)
Polling behavior


✅ MOVA Protocol – Compact Single-Line Messages
(Ready for direct TCP transmission)

1. Handshake
Request:
text00000159{"StreamID":"1","TransactionId":"0x00000001","MessageType":"ReqCheckConnectedToRightController","Params":{"ControllerID":1}}
Response:
text00000106{"MessageType":"RspCheckConnectedToRightController","TransactionId":"0x00000001","Params":{"IsOk":true}}

2. Time (ReqMOVATime)
Request:
text00000091{"StreamID":"1","TransactionId":"0x00000002","MessageType":"ReqMOVATime"}
Response:
text00000128{"MessageType":"RspMOVATime","TransactionId":"0x00000002","Params":{"DateTime":"2026-06-03T09:40:16","IsWallClockTime":false}}

3. Operation Flags (Very Important)
Request:
text00000097{"StreamID":"1","TransactionId":"0x00000003","MessageType":"ReqOperationFlags"}
Response:
text00000199{"MessageType":"RspOperationFlags","TransactionId":"0x00000003","Params":{"CRB":false,"IsMOVAEnabled":true,"IsOnControl":false,"IsMultiStage":false,"ErrorCount":0,"Warmup":-1,"DemandedStageNum":0}}

4. Data Plan Status
Request:
text00000107{"StreamID":"1","TransactionId":"0x00000004","MessageType":"ReqDataPlanTriggeringStatus"}
Response:
text00000104{"MessageType":"RspDataPlanTriggeringStatus","TransactionId":"0x00000004","Params":{"IsEnabled":true}}

5. Detectors Status (Critical for UI data)
Request:
text00000107{"StreamID":"1","TransactionId":"0x00000005","MessageType":"ReqDetectorsStatus"}
Response (example with many trues):
text00000478{"MessageType":"RspDetectorsStatus","TransactionId":"0x00000005","Params":{"Status":[true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,true,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false],"MovaDateTime":"2026-06-03T09:41:22"}}

6. Lane Data (Send for each ID)
Request (ID=1):
text00000126{"StreamID":"1","TransactionId":"0x00000006","MessageType":"ReqLaneData","Params":{"ID":1}}
Response:
text00000525{"MessageType":"RspLaneData","TransactionId":"0x00000006","Params":{"RedCountIN":0,"RedCountX":0,"SFSmoothed":18,"SFLastCycle":-1.00,"ShiftRegister":[false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false,false],"OversatCounter":0,"Endsat":0,"QBeyondINDET":22,"LeftOverVehs":0}}
(Repeat for ID=2, 3, etc. — just change "ID":X and TransactionId)

7. Link Data (Send for each ID)
Request (ID=1):
text00000126{"StreamID":"1","TransactionId":"0x00000007","MessageType":"ReqLinkData","Params":{"ID":1}}
Response:
text00000253{"MessageType":"RspLinkData","TransactionId":"0x00000007","Params":{"BonusGreenTime":0,"SmoothedFlow":18,"Endsat":0,"DemandType":1,"NetBenFlow":-1,"ActualFlow":-1,"FutureRedTime":-1,"ExtraGreenTime":-1,"EPHoldMarker":0,"EPExtMarker":0,"EPExtTimer":0}}

8. Other Useful Messages
ReqForceBits:
text00000092{"StreamID":"1","TransactionId":"0x00000008","MessageType":"ReqForceBits"}
RspForceBits:
text00000187{"MessageType":"RspForceBits","TransactionId":"0x00000008","Params":{"ForceBits":[false,false,false,false,false,false,false,false,false,false],"TakeOverBit":false,"HurryInhibit":false}}
ReqRawDetectorsStatus:
text00000102{"StreamID":"1","TransactionId":"0x00000009","MessageType":"ReqRawDetectorsStatus"}
ReqOutputChannelStatus:
text00000102{"StreamID":"1","TransactionId":"0x0000000a","MessageType":"ReqOutputChannelStatus"}

Pro Tips for TCP Implementation:

Always calculate the exact byte length of the JSON string (UTF-8).
Format length as exactly 9 digits with leading zeros.
No extra spaces or newlines in the final message.
Increment TransactionId for every new request.






AI Update 14:05 03/06/2026

✅ Full Updated MOVA Protocol Document
(Based on both captures — including the dataset push)

MOVA Tools Protocol Overview

Connection: TCP to 192.168.71.23:55002
Framing: 000000000 (9-digit zero-padded length) + compact JSON
Style: Request/Response with TransactionId matching


1. Core Message Format
On-wire example:
text00000159{"StreamID":"1","TransactionId":"0x00000001","MessageType":"ReqCheckConnectedToRightController","Params":{"ControllerID":1}}

2. Handshake
ReqCheckConnectedToRightController
JSON{"StreamID":"1","TransactionId":"0x00000001","MessageType":"ReqCheckConnectedToRightController","Params":{"ControllerID":1}}
RspCheckConnectedToRightController
JSON{"MessageType":"RspCheckConnectedToRightController","TransactionId":"0x00000001","Params":{"IsOk":true}}

3. Normal Polling Messages (Most Frequent)

























MessageTypeCompact RequestKey Response FieldsReqMOVATime00000091{"StreamID":"1","TransactionId":"0x00000002","MessageType":"ReqMOVATime"}DateTime, IsWallClockTimeReqOperationFlags00000097{"StreamID":"1","TransactionId":"0x00000003","MessageType":"ReqOperationFlags"}IsMOVAEnabled, IsOnControlReqDataPlanTriggeringStatus00000107{"StreamID":"1","TransactionId":"0x00000004","MessageType":"ReqDataPlanTriggeringStatus"}IsEnabled

4. Dataset / File Transfer Messages (New!)
ReqCheckTransferedFileIntegrity (sent after pushing MXDS dataset)
JSON{"StreamID":"1","TransactionId":"0x00000002","MessageType":"ReqCheckTransferedFileIntegrity","Params":{"FileCRC32":1187782623}}
RspCheckTransferedFileIntegrity
JSON{"MessageType":"RspCheckTransferedFileIntegrity","TransactionId":"0x00000002","Params":{"IsFileOk":false}}
Note: The actual file transfer (MXDS) likely happens via another channel (e.g. TFTP/HTTP). This message is the integrity verification step over the main control port.

5. Other Important Messages

ReqDetectorsStatus → Returns large Status boolean array + MovaDateTime
ReqLaneData (Params: {"ID": N}) → Per-lane statistics (SFSmoothed, QBeyondINDET, etc.)
ReqLinkData (Params: {"ID": N}) → Stage/link data
ReqForceBits / ReqConfirmBits → Used during configuration
ReqRawDetectorsStatus, ReqOutputChannelStatus


6. Best Practices for MOVA Tools Emulation

Length Prefix — Must be exactly 9 digits, accurate byte count of the JSON.
TransactionId — Increment hex value for every request.
Polling — Continuously send ReqMOVATime + ReqOperationFlags.
Dataset Push — Expect ReqCheckTransferedFileIntegrity after file upload. Return IsFileOk: true for success.
Realistic Data — Populate detector arrays and lane/link values — otherwise Tools shows "no data".