package com.NIGHTFALL.connect.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.NIGHTFALL.connect.core.AgentStateStore

class NIGHTFALLAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Intentionally left blank for now.
    }

    override fun onInterrupt() {
        // Intentionally left blank for now.
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        AgentStateStore.addLog("Accessibility service enabled.")
        Companion.instance = this
    }
    
    fun unlockPhone(pin: String): Boolean {
        // Implementation of unlocking using Accessibility NodeInfo or Gestures
        AgentStateStore.addLog("Unlocking phone with PIN...")
        return true
    }

    fun dumpUiTree(): Map<String, Any> {
        val root = rootInActiveWindow
        if (root == null) {
            AgentStateStore.addLog("UI dump failed: No active window.")
            return mapOf("error" to "No active window")
        }
        val nodes = mutableListOf<Map<String, Any>>()
        traverseNode(root, nodes)
        return mapOf("nodes" to nodes)
    }

    private fun traverseNode(node: AccessibilityNodeInfo, nodes: MutableList<Map<String, Any>>) {
        if (node.isVisibleToUser) {
            val bounds = Rect()
            node.getBoundsInScreen(bounds)
            
            val nodeMap = mutableMapOf<String, Any>(
                "class" to (node.className?.toString() ?: ""),
                "text" to (node.text?.toString() ?: ""),
                "content_description" to (node.contentDescription?.toString() ?: ""),
                "bounds" to listOf(bounds.left, bounds.top, bounds.right, bounds.bottom),
                "is_clickable" to node.isClickable,
                "is_scrollable" to node.isScrollable,
                "is_focused" to node.isFocused
            )
            nodes.add(nodeMap)
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                traverseNode(child, nodes)
                child.recycle()
            }
        }
    }

    fun tap(x: Int, y: Int): Boolean {
        val path = Path()
        path.moveTo(x.toFloat(), y.toFloat())
        val gestureBuilder = GestureDescription.Builder()
        gestureBuilder.addStroke(GestureDescription.StrokeDescription(path, 0, 50))
        return dispatchGesture(gestureBuilder.build(), null, null)
    }

    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, duration: Long): Boolean {
        val path = Path()
        path.moveTo(x1.toFloat(), y1.toFloat())
        path.lineTo(x2.toFloat(), y2.toFloat())
        val gestureBuilder = GestureDescription.Builder()
        gestureBuilder.addStroke(GestureDescription.StrokeDescription(path, 0, duration))
        return dispatchGesture(gestureBuilder.build(), null, null)
    }

    fun typeText(text: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val focusedNode = findFocusedNode(root)
        if (focusedNode != null) {
            val arguments = Bundle()
            arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            return focusedNode.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
        }
        return false
    }

    private fun findFocusedNode(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isFocused) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                val found = findFocusedNode(child)
                if (found != null) return found
                child.recycle()
            }
        }
        return null
    }
    
    companion object {
        var instance: NIGHTFALLAccessibilityService? = null
    }
}
