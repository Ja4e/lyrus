# Maintainer: Ja4e Jakie101@proton.me
pkgname=lyrus
pkgrel=1
pkgdesc="A lyrics synchronization project"
arch=('any')
url="https://github.com/Ja4e/Lyrus"
license=('MIT')
depends=('python' 'python-requests' 'python-aiohttp' 'python-syncedlyrics' 'python-mpd2' 'python-appdirs') # for its only for Arch beaware
makedepends=('git')
source=("git+https://github.com/Ja4e/Lyrus.git")
sha256sums=('SKIP')

pkgver() {
    cd "$srcdir/Lyrus"
    local tag=$(git describe --tags --abbrev=0 2>/dev/null || echo "0.0.0")
    local commits=$(git rev-list "${tag}..HEAD" --count 2>/dev/null || echo "0")
    local hash=$(git rev-parse --short HEAD)
    echo "${tag}+${commits}.g${hash}"
}

package() {
    install -Dm755 "$srcdir/Lyrus/lyrus.py" "$pkgdir/usr/bin/lyrus"
    install -Dm644 "$srcdir/Lyrus/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -Dm644 "$srcdir/Lyrus/README.md" "$pkgdir/usr/share/doc/$pkgname/README.md"
}
# makepkg -si and have fun
