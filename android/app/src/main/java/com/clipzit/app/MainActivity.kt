package com.clipzit.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.clipzit.app.ui.ClipBg
import com.clipzit.app.ui.ClipSurface
import com.clipzit.app.ui.ClipYellow
import com.clipzit.app.ui.ClipzitTheme
import com.clipzit.app.ui.EditorScreen
import com.clipzit.app.ui.ExportsScreen
import com.clipzit.app.ui.HomeScreen
import com.clipzit.app.ui.ProjectScreen
import com.clipzit.app.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ClipzitTheme {
                ClipzitApp()
            }
        }
    }
}

@Composable
fun ClipzitApp(vm: AppViewModel = viewModel()) {
    val snackbar = remember { SnackbarHostState() }
    val screen = vm.screen

    BackHandler(enabled = screen != Screen.Home) { vm.back() }

    LaunchedEffect(vm.message) {
        val m = vm.message
        if (m != null) {
            snackbar.showSnackbar(m)
            vm.message = null
        }
    }

    Scaffold(
        containerColor = ClipBg,
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            if (screen == Screen.Home || screen == Screen.Settings || screen == Screen.Exports) {
                NavigationBar(containerColor = ClipSurface) {
                    val colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = androidx.compose.ui.graphics.Color.Black,
                        selectedTextColor = ClipYellow,
                        indicatorColor = ClipYellow,
                    )
                    NavigationBarItem(
                        selected = screen == Screen.Home,
                        onClick = { vm.goHome() },
                        icon = { Icon(Icons.Default.Home, contentDescription = null) },
                        label = { Text("Projects") },
                        colors = colors,
                    )
                    NavigationBarItem(
                        selected = screen == Screen.Exports,
                        onClick = { vm.openExports() },
                        icon = { Icon(Icons.Default.List, contentDescription = null) },
                        label = { Text("Exports") },
                        colors = colors,
                    )
                    NavigationBarItem(
                        selected = screen == Screen.Settings,
                        onClick = { vm.openSettings() },
                        icon = { Icon(Icons.Default.Settings, contentDescription = null) },
                        label = { Text("Settings") },
                        colors = colors,
                    )
                }
            }
        },
    ) { inner ->
        val modifier = Modifier.padding(inner)
        when (screen) {
            Screen.Home -> HomeScreen(vm, modifier)
            Screen.Exports -> ExportsScreen(vm, modifier)
            Screen.Settings -> SettingsScreen(vm, modifier)
            is Screen.Project -> ProjectScreen(vm, screen.id, modifier)
            is Screen.Editor -> EditorScreen(vm, screen.projectId, screen.clipId, modifier)
        }
    }
}
