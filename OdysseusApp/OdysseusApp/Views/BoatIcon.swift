import SwiftUI

struct BoatIcon: View {
    var body: some View {
        ZStack {
            Path { p in
                p.move(to: CGPoint(x: 16, y: 8))
                p.addLine(to: CGPoint(x: 16, y: 22))
                p.addLine(to: CGPoint(x: 24, y: 22))
                p.closeSubpath()
            }
            .fill()
            .opacity(0.6)

            Path { p in
                p.move(to: CGPoint(x: 16, y: 4))
                p.addLine(to: CGPoint(x: 16, y: 22))
                p.addLine(to: CGPoint(x: 6, y: 22))
                p.closeSubpath()
            }
            .fill()

            Path { p in
                p.move(to: CGPoint(x: 4, y: 24))
                p.addQuadCurve(to: CGPoint(x: 16, y: 24), control: CGPoint(x: 10, y: 20))
                p.addQuadCurve(to: CGPoint(x: 28, y: 24), control: CGPoint(x: 22, y: 28))
            }
            .stroke(style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
        }
        .aspectRatio(1, contentMode: .fit)
    }
}
