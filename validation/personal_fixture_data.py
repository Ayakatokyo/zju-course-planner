"""Small valid, text-based PDFs containing only synthetic test material."""


def synthetic_pdf(version="1"):
    """Build two physical pages using the PDF standard's built-in font.

    English ASCII keeps this fixture independent of installed fonts. Explicit
    exclusions on the following page exercise retention of required context.
    No real school document, student or curriculum rule appears in the file.
    """
    pages = [
        ["SYNTHETIC CURRICULUM - LOCAL SEARCH TEST", "Version " + version,
         "This is synthetic validation material. It is not a school policy.", "",
         "Module                 Course code       Nominal credits",
         "Synthetic AI           SYNTHETIC-001     3",
         "Synthetic seminar      SYNTHETIC-002     2", "",
         "SYNTHETIC-001 requires the synthetic prerequisite SYNTHETIC-000.",
         "See the exclusions and applicability note on physical page 2."],
        ["SYNTHETIC CURRICULUM - EXCLUSIONS", "Version " + version, "",
         "SYNTHETIC-001 does not count toward the synthetic seminar module.",
         "Only the fictional cohort SYNTHETIC-2023 is described here.",
         "An observed grade is not proof of earned credits or recognition.",
         "Applicability and all rules remain unreviewed.", "",
         "This page is required context for the table on physical page 1."],
    ]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [4 0 R 6 0 R] /Count 2 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    for index, lines in enumerate(pages):
        content_id = 5 + index * 2
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_id).encode())
        escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
        stream = ("BT /F1 10 Tf 42 740 Td 18 TL\n" + "\n".join("(" + line + ") Tj T*" for line in escaped) + "\nET").encode("ascii")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(("xref\n0 %d\n0000000000 65535 f \n" % len(offsets)).encode())
    for offset in offsets[1:]:
        result.extend(("%010d 00000 n \n" % offset).encode())
    result.extend(("trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(offsets), xref)).encode())
    return bytes(result)
