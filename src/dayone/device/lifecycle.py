"""Record lifecycle: an explicit state machine, shared by the phone and the server.

    CAPTURED -> PENDING_AI -> AI_PROCESSED -> NEEDS_REVIEW -> VALIDATED
             -> PATIENT_MATCHED -> REGISTERED -> SYNCED

plus failure states. Every transition is checked against ``TRANSITIONS`` and appended to
the record's history (who, when, why), so the state of any record can be explained.
"""

from __future__ import annotations

from enum import StrEnum


class RecordState(StrEnum):
    CAPTURED = "CAPTURED"  # photos taken, stored encrypted on the phone
    PENDING_AI = "PENDING_AI"  # queued for AI processing (waits for connectivity)
    AI_PROCESSED = "AI_PROCESSED"  # extraction received from the processing server
    NEEDS_REVIEW = "NEEDS_REVIEW"  # midwife is verifying fields
    VALIDATED = "VALIDATED"  # every field confirmed / corrected / entered by the midwife
    PATIENT_MATCHED = "PATIENT_MATCHED"  # linked to an existing or a new patient profile
    REGISTERED = "REGISTERED"  # written into the patient's longitudinal record on the phone
    SYNCED = "SYNCED"  # acknowledged by the server
    # failure / exception states
    PROCESSING_FAILED = "PROCESSING_FAILED"  # the AI step failed (retried automatically)
    SYNC_FAILED = "SYNC_FAILED"  # the server rejected or did not acknowledge the record (retried)
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"  # same patient + same pages already registered
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"  # AI cannot help: manual entry / supervisor decision


STATE_LABEL_FR = {
    RecordState.CAPTURED: "CAPTURÉ", RecordState.PENDING_AI: "EN_ATTENTE_IA", RecordState.AI_PROCESSED: "TRAITÉ_IA",
    RecordState.NEEDS_REVIEW: "À_RÉVISER", RecordState.VALIDATED: "VALIDÉ", RecordState.PATIENT_MATCHED: "PATIENTE_LIÉE",
    RecordState.REGISTERED: "ENREGISTRÉ", RecordState.SYNCED: "SYNCHRONISÉ",
    RecordState.PROCESSING_FAILED: "ÉCHEC_TRAITEMENT", RecordState.SYNC_FAILED: "ÉCHEC_SYNCHRONISATION",
    RecordState.DUPLICATE_SUSPECTED: "DOUBLON_SUSPECTÉ", RecordState.MANUAL_REVIEW_REQUIRED: "RÉVISION_MANUELLE_REQUISE",
}

S = RecordState
TRANSITIONS: dict[RecordState, set[RecordState]] = {
    S.CAPTURED: {S.PENDING_AI, S.MANUAL_REVIEW_REQUIRED},
    S.PENDING_AI: {S.AI_PROCESSED, S.PROCESSING_FAILED, S.MANUAL_REVIEW_REQUIRED},
    S.PROCESSING_FAILED: {S.MANUAL_REVIEW_REQUIRED},  # jobs are retried while PENDING_AI; this is final
    S.AI_PROCESSED: {S.NEEDS_REVIEW, S.PENDING_AI},
    S.NEEDS_REVIEW: {S.VALIDATED, S.PENDING_AI, S.MANUAL_REVIEW_REQUIRED},  # PENDING_AI: a page was retaken
    # PENDING_AI: "retry the AI"; NEEDS_REVIEW: fields read by the AI before it failed remain to review
    S.MANUAL_REVIEW_REQUIRED: {S.VALIDATED, S.PENDING_AI, S.NEEDS_REVIEW},
    S.VALIDATED: {S.PATIENT_MATCHED, S.DUPLICATE_SUSPECTED, S.MANUAL_REVIEW_REQUIRED, S.NEEDS_REVIEW},
    S.DUPLICATE_SUSPECTED: {S.PATIENT_MATCHED, S.MANUAL_REVIEW_REQUIRED},
    S.PATIENT_MATCHED: {S.REGISTERED},
    S.REGISTERED: {S.SYNCED, S.SYNC_FAILED},
    S.SYNC_FAILED: {S.SYNCED},
    S.SYNCED: set(),  # corrections after synchronisation are out of the prototype's scope
}

# States from which the record still needs the network (outbox work pending).
NEEDS_NETWORK = {S.PENDING_AI, S.PROCESSING_FAILED, S.REGISTERED, S.SYNC_FAILED}


class InvalidTransition(Exception):
    pass


def check_transition(current: RecordState, target: RecordState) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"{current} -> {target} is not allowed")
