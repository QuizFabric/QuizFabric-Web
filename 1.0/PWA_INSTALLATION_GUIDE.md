# QuizFabric Progressive Web App (PWA) Installation Guide

Your QuizFabric application is now configured as a Progressive Web App that can be installed on iOS, iPadOS, and Android devices!

## What Was Added

### 1. **manifest.json** (`/static/manifest.json`)
The web app manifest defines how your app appears when installed:
- **App Name**: QuizFabric
- **Short Name**: QuizFabric  
- **Display**: Standalone (looks like a native app)
- **Theme Color**: Purple (#4f46e5)
- **App Icon**: Uses your existing `/data/icon.png`
- **Categories**: Education, Productivity

### 2. **Service Worker** (`/static/service-worker.js`)
Provides offline support and caching:
- Caches essential assets (CSS, JS, icons)
- Serves cached content when offline
- Automatically updates cache with new versions
- Handles network requests intelligently

### 3. **Meta Tags** (All HTML Templates)
Added PWA-specific meta tags to all 17 HTML templates:
- `viewport-fit=cover` - Supports iPhone notch/Dynamic Island
- `apple-mobile-web-app-capable` - Enables iOS installation
- `apple-mobile-web-app-status-bar-style` - Dark status bar
- `apple-mobile-web-app-title` - "QuizFabric" in app switcher
- `apple-touch-icon` - Home screen icon
- `manifest` link - Points to manifest.json
- `theme-color` - Browser/app theme color

### 4. **Service Worker Registration** (Key Pages)
Added automatic registration in:
- `index.html`
- `home.html` 
- `quiz.html`
- `addquiz.html`
- `makequiz.html`
- `join.html`
- `host.html`
- All other templates

## How to Install on iOS/iPadOS

1. **Open Safari** (not Chrome) and navigate to your QuizFabric URL
2. **Tap Share** button (bottom toolbar on iPhone, top-right on iPad)
3. **Scroll down** and tap "Add to Home Screen"
4. **Name it** "QuizFabric" (or your preferred name)
5. **Tap Add** - The app icon will appear on your home screen
6. **Tap the icon** to launch QuizFabric as a full-screen app

## How to Install on Android

1. **Open Chrome** and navigate to your QuizFabric URL
2. **Tap the menu** (three dots, top-right)
3. **Tap "Install app"** or **"Add to Home screen"**
4. **Confirm** - The app will be installed

## Features Provided by PWA

✅ **Home Screen Installation** - Install as a native-looking app
✅ **Full Screen Experience** - No browser UI when installed
✅ **Custom App Icon** - Your QuizFabric icon on home screen
✅ **Offline Support** - Basic offline functionality via service worker
✅ **Status Bar Styling** - Custom iOS status bar appearance
✅ **Fast Load Times** - Assets cached for quick startup
✅ **Responsive Design** - Works on all device sizes

## Icon Requirements

The app currently uses `/data/icon.png` as the app icon. For best results:
- **Minimum size**: 192x192 pixels
- **Recommended size**: 512x512 pixels (for high-res displays)
- **Format**: PNG with transparency
- **Design**: Keep padding around the icon content for iOS

If you want to optimize the icon experience, you can add larger versions in `manifest.json`:
- 192x192 for standard devices
- 512x512 for high-res devices
- 256x256 as intermediate size

## Configuration Details

### Manifest.json Customization
If you want to customize the app further, edit `/static/manifest.json`:
- Change `name` or `short_name` for different branding
- Adjust `theme_color` to match your brand
- Modify `background_color` for splash screen
- Update `categories` for app store classification

### Service Worker Cache
The service worker caches:
- CSS stylesheets
- JavaScript files
- The app icon
- HTML pages (dynamically)

To update what's cached, edit `/static/service-worker.js` and modify the `urlsToCache` array.

## Browser Compatibility

| Browser | Support |
|---------|---------|
| Safari (iOS) | ✅ Full Support |
| Chrome (Android) | ✅ Full Support |
| Firefox (Android) | ✅ Full Support |
| Safari (macOS) | ✅ Partial Support |
| Chrome (Desktop) | ✅ Full Support |
| Edge (Desktop) | ✅ Full Support |

## Troubleshooting

### App Not Appearing on iOS?
- Make sure you're using Safari (not Chrome)
- Enable JavaScript in Settings
- Check that the website is HTTPS or localhost
- Clear Safari cache and try again

### Icon Not Showing?
- Verify `/data/icon.png` exists and is accessible
- Try clearing app cache (Settings > Safari > Clear History and Website Data)
- Ensure icon is at least 192x192 pixels

### Service Worker Not Registering?
- Check browser console for errors (Developer Tools)
- Verify service-worker.js is in `/static/` directory
- Ensure HTTPS or localhost (service workers require secure context)

## Next Steps

1. **Test Installation**: Try installing the app on your device
2. **Customize Icon**: Replace `/data/icon.png` with a higher-res version if desired
3. **Set Splash Screen**: Add `screenshots` to manifest.json for better app preview
4. **Monitor Performance**: Check if offline caching is working as expected

---

Your QuizFabric app is now ready to be installed on iOS and Android devices! 🚀
