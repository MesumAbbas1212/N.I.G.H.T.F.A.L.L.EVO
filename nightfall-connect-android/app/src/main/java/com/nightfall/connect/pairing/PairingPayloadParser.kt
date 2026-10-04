package com.NIGHTFALL.connect.pairing

import com.NIGHTFALL.connect.core.PairingOffer
import org.json.JSONObject

object PairingPayloadParser {
    fun parse(raw: String): PairingOffer? {
        if (raw.isBlank()) return null
        return try {
            PairingOffer.fromJson(JSONObject(raw))
        } catch (_: Exception) {
            null
        }
    }
}
